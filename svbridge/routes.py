from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import Response

from .auth import AuthProvider
from .config import AppConfig
from .convert import openai_to_gemini
from .proxy import proxy_gemini_as_openai, stream_proxy

logger = logging.getLogger("svbridge")

# These are set by main.py at startup
auth: AuthProvider = None  # type: ignore[assignment]
http_client: httpx.AsyncClient = None  # type: ignore[assignment]
app_config: AppConfig = None  # type: ignore[assignment]


def init(cfg: AppConfig, auth_provider: AuthProvider, client: httpx.AsyncClient) -> None:
    global auth, http_client, app_config
    app_config = cfg
    auth = auth_provider
    http_client = client


async def verify_token(request: Request, authorization: str | None = Header(None)) -> None:
    if not app_config.proxy_key:
        return
    # Prefer Authorization header when present
    if authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            if secrets.compare_digest(parts[1], app_config.proxy_key):
                return
            logger.warning(f"[Auth] 401 Invalid Bearer token | {request.method} {request.url.path}")
            raise HTTPException(status_code=401, detail="Invalid token")
        logger.warning(
            f"[Auth] 401 Invalid Authorization header format | {request.method} {request.url.path}"
        )
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    # Check x-goog-api-key header (used by native Gemini SDK clients)
    goog_key = request.headers.get("x-goog-api-key")
    if goog_key:
        if secrets.compare_digest(goog_key, app_config.proxy_key):
            return
        logger.warning(f"[Auth] 401 Invalid x-goog-api-key | {request.method} {request.url.path}")
        raise HTTPException(status_code=401, detail="Invalid key")
    # Fall back to ?key= query parameter for Gemini API clients
    key_param = request.query_params.get("key")
    if key_param is not None:
        if secrets.compare_digest(key_param, app_config.proxy_key) if key_param else False:
            return
        logger.warning(
            f"[Auth] 401 Invalid ?key= query param | {request.method} {request.url.path}"
        )
        raise HTTPException(status_code=401, detail="Invalid key")
    logger.warning(f"[Auth] 401 Missing Authorization header | {request.method} {request.url.path}")
    raise HTTPException(status_code=401, detail="Missing Authorization header")


router = APIRouter(prefix="/v1", dependencies=[Depends(verify_token)])


CLIENT_VERSION = "simple-vertex-bridge/0.4.1"


def _normalize_model(model: str) -> str:
    """Auto-prefix 'google/' if model name has no publisher prefix."""
    if "/" not in model:
        return f"google/{model}"
    return model


def _forward_query(request: Request) -> str:
    """Build URL-encoded query string from request, stripping the proxy auth key."""
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
    return urlencode(params) if params else ""


def _proxy_headers(request: Request, auth_headers: dict[str, str]) -> dict[str, str]:
    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "authorization", "content-length", "x-goog-api-key")
    }
    headers.update(auth_headers)
    headers["x-goog-api-client"] = CLIENT_VERSION
    return headers


# --- OpenAI-compatible endpoint ---


@router.api_route("/chat/completions", methods=["POST"])
async def chat_completions(request: Request):
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    raw_model = body.get("model", "")
    is_stream = bool(body.get("stream", False))

    if app_config.auth_mode == "service_account":
        model = _normalize_model(raw_model)
        body["model"] = model
        url = auth.build_openai_url("/chat/completions", model=model)
        qs = _forward_query(request)
        if qs:
            url += "?" + qs
        headers = _proxy_headers(request, await auth.get_headers())
        payload = json.dumps(body).encode()
        tag = f"[Chat] {model} | stream={is_stream} (SA)"
        return await stream_proxy(http_client, request.method, url, headers, payload, log_tag=tag)

    # API key / AI Studio mode: convert OpenAI -> Gemini -> OpenAI
    model, gemini_body, is_stream = openai_to_gemini(body)

    method = "streamGenerateContent" if is_stream else "generateContent"
    url = auth.build_gemini_url(model, method)
    if is_stream:
        url += "&alt=sse" if "?" in url else "?alt=sse"

    headers = {"Content-Type": "application/json"}
    headers.update(await auth.get_headers())
    headers["x-goog-api-client"] = CLIENT_VERSION
    payload = json.dumps(gemini_body).encode()

    tag = f"[Chat] {model} | stream={is_stream}"
    return await proxy_gemini_as_openai(
        http_client, url, headers, payload, model, is_stream, log_tag=tag
    )


def _parse_model_path(model_path: str) -> str:
    """Parse model path, strip 'models/' or publisher prefix if present."""
    if not model_path:
        raise HTTPException(status_code=400, detail="Invalid model path")
    model_path = model_path.strip("/")
    for prefix in ("v1beta/", "v1beta1/", "v1/"):
        if model_path.startswith(prefix):
            model_path = model_path.removeprefix(prefix)
    if model_path.startswith("models/"):
        model_path = model_path.removeprefix("models/")
    parts = [p for p in model_path.split("/") if p]
    if not parts:
        raise HTTPException(status_code=400, detail="Invalid model path")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2 and parts[0] == "google":
        return parts[1]
    if len(parts) == 4 and parts[0] == "publishers" and parts[2] == "models":
        return parts[3] if parts[1] == "google" else f"{parts[1]}/{parts[3]}"
    return "/".join(parts)


# --- Gemini native endpoints ---

gemini_router = APIRouter(dependencies=[Depends(verify_token)])


@gemini_router.api_route("/models/{model_path:path}:generateContent", methods=["POST"])
async def generate_content(model_path: str, request: Request):
    model = _parse_model_path(model_path)
    url = auth.build_gemini_url(model, "generateContent")
    qs = _forward_query(request)
    if qs:
        url += f"&{qs}" if "?" in url else f"?{qs}"
    headers = _proxy_headers(request, await auth.get_headers())
    headers["Content-Type"] = "application/json"
    body = await request.body()
    start_t = time.perf_counter()
    resp = await http_client.post(url, headers=headers, content=body)
    duration_ms = int((time.perf_counter() - start_t) * 1000)
    level = logger.info if resp.status_code == 200 else logger.warning
    level(f"[Native] {model}:generateContent | status={resp.status_code} ({duration_ms}ms)")
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
    )


@gemini_router.api_route("/models/{model_path:path}:streamGenerateContent", methods=["POST"])
async def stream_generate_content(model_path: str, request: Request):
    model = _parse_model_path(model_path)
    url = auth.build_gemini_url(model, "streamGenerateContent")
    qs = _forward_query(request)
    if qs:
        url += f"&{qs}" if "?" in url else f"?{qs}"
    headers = _proxy_headers(request, await auth.get_headers())
    headers["Content-Type"] = "application/json"
    body = await request.body()
    tag = f"[Native] {model}:streamGenerateContent"
    return await stream_proxy(http_client, "POST", url, headers, body, log_tag=tag)


# --- Model Catalog & Formatting Helpers ---


async def get_model_catalog() -> list[dict]:
    start_t = time.perf_counter()

    async def _fetch(publisher: str) -> list[dict]:
        url = auth.build_models_url(publisher)
        headers = {"Content-Type": "application/json", "x-goog-api-client": CLIENT_VERSION}
        auth_headers = await auth.get_headers()
        headers.update(auth_headers)

        for attempt in range(3):
            try:
                resp = await http_client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"[Models] {publisher} returned status {resp.status_code}")
                    return []
                data = resp.json()
                result = []

                # AI Studio format: {"models": [{"name": "models/gemini-..."}]}
                if "models" in data:
                    for m in data["models"]:
                        name = m.get("name", "")
                        model_id = name.removeprefix("models/")
                        item = {
                            "id": model_id,
                            "displayName": m.get("displayName") or model_id,
                            "description": m.get("description", ""),
                            "owned_by": "google",
                            "supportedGenerationMethods": m.get(
                                "supportedGenerationMethods", ["generateContent", "countTokens"]
                            ),
                        }
                        for field in (
                            "inputTokenLimit",
                            "outputTokenLimit",
                            "temperature",
                            "topP",
                            "topK",
                        ):
                            if field in m:
                                item[field] = m[field]
                        result.append(item)

                    # Handle pagination
                    while data.get("nextPageToken"):
                        sep = "&" if "?" in url else "?"
                        page_url = f"{url}{sep}pageToken={data['nextPageToken']}"
                        resp = await http_client.get(page_url, headers=headers)
                        if resp.status_code != 200:
                            break
                        data = resp.json()
                        for m in data.get("models", []):
                            name = m.get("name", "")
                            model_id = name.removeprefix("models/")
                            item = {
                                "id": model_id,
                                "displayName": m.get("displayName") or model_id,
                                "description": m.get("description", ""),
                                "owned_by": "google",
                                "supportedGenerationMethods": m.get(
                                    "supportedGenerationMethods", ["generateContent", "countTokens"]
                                ),
                            }
                            for field in (
                                "inputTokenLimit",
                                "outputTokenLimit",
                                "temperature",
                                "topP",
                                "topK",
                            ):
                                if field in m:
                                    item[field] = m[field]
                            result.append(item)
                    return result

                # Vertex format: {"publisherModels": [...]}
                for m in data.get("publisherModels", []):
                    name = m.get("name", "")
                    parts = name.split("/")
                    if len(parts) == 4 and parts[0] == "publishers" and parts[2] == "models":
                        pub, model_name = parts[1], parts[3]
                        model_id = model_name if pub == "google" else f"{pub}/{model_name}"
                        item = {
                            "id": model_id,
                            "displayName": m.get("displayName") or model_name,
                            "description": m.get("description", ""),
                            "owned_by": pub,
                            "supportedGenerationMethods": ["generateContent", "countTokens"],
                        }
                        result.append(item)
                return result
            except httpx.RequestError as e:
                if attempt < 2:
                    await asyncio.sleep(0.2)
                    continue
                logger.warning(f"[Models] {publisher} request failed: {e}")
                return []
        return []

    pubs = ["google"] if app_config.auth_mode == "aistudio" else app_config.publishers
    tasks = [_fetch(pub) for pub in pubs]
    results = await asyncio.gather(*tasks)

    all_models: list[dict] = []
    for models_list in results:
        all_models.extend(models_list)

    if app_config.filter_model_names:
        all_models = [
            m
            for m in all_models
            if any(m["id"].startswith(prefix) for prefix in app_config.model_names_filter)
        ]

    for model_id in app_config.extra_models:
        owner = model_id.split("/")[0] if "/" in model_id else "google"
        all_models.append(
            {
                "id": model_id,
                "displayName": model_id,
                "description": f"{model_id} model",
                "owned_by": owner,
                "supportedGenerationMethods": ["generateContent", "countTokens"],
            }
        )

    seen: set[str] = set()
    deduped_models: list[dict] = []
    for m in all_models:
        if m["id"] not in seen:
            seen.add(m["id"])
            deduped_models.append(m)

    duration_ms = int((time.perf_counter() - start_t) * 1000)
    logger.info(f"[Models] catalog | {len(deduped_models)} models fetched ({duration_ms}ms)")
    return deduped_models


def _is_gemini_format_request(request: Request) -> bool:
    """Determine whether the request expects Gemini format vs OpenAI format."""
    path = request.url.path
    if path.startswith(("/v1beta", "/v1beta1")):
        return True
    fmt = request.query_params.get("format", "").lower()
    if fmt == "gemini":
        return True
    if fmt == "openai":
        return False
    if request.headers.get("x-goog-api-key"):
        return True
    if "key" in request.query_params and not request.headers.get("authorization"):
        return True
    if "x-goog-api-client" in request.headers:
        return True
    return path == "/models" or (path.startswith("/models/") and not path.startswith("/v1/"))


def _format_openai_models(models: list[dict]) -> dict:
    return {
        "object": "list",
        "data": [
            {
                "id": m["id"],
                "object": "model",
                "created": 1700000000,
                "owned_by": m.get("owned_by", "google"),
            }
            for m in models
        ],
    }


def _format_gemini_models(models: list[dict]) -> dict:
    gemini_list = []
    for m in models:
        model_id = m["id"]
        name = f"models/{model_id}" if not model_id.startswith("models/") else model_id
        entry = {
            "name": name,
            "version": m.get("version", "001"),
            "displayName": m.get("displayName") or model_id,
            "description": m.get("description") or f"{model_id} model",
            "supportedGenerationMethods": m.get(
                "supportedGenerationMethods", ["generateContent", "countTokens"]
            ),
        }
        for field in ("inputTokenLimit", "outputTokenLimit", "temperature", "topP", "topK"):
            if m.get(field) is not None:
                entry[field] = m[field]
        gemini_list.append(entry)
    return {"models": gemini_list}


def _format_openai_single_model(m: dict) -> dict:
    return {
        "id": m["id"],
        "object": "model",
        "created": 1700000000,
        "owned_by": m.get("owned_by", "google"),
    }


def _format_gemini_single_model(m: dict) -> dict:
    model_id = m["id"]
    name = f"models/{model_id}" if not model_id.startswith("models/") else model_id
    entry = {
        "name": name,
        "version": m.get("version", "001"),
        "displayName": m.get("displayName") or model_id,
        "description": m.get("description") or f"{model_id} model",
        "supportedGenerationMethods": m.get(
            "supportedGenerationMethods", ["generateContent", "countTokens"]
        ),
    }
    for field in ("inputTokenLimit", "outputTokenLimit", "temperature", "topP", "topK"):
        if m.get(field) is not None:
            entry[field] = m[field]
    return entry


# --- Model listing routes ---


@router.api_route("/models", methods=["GET"])
async def models(request: Request):
    models_list = await get_model_catalog()
    if _is_gemini_format_request(request):
        return _format_gemini_models(models_list)
    return _format_openai_models(models_list)


@router.api_route("/models/{model_path:path}", methods=["GET"])
async def get_model(model_path: str, request: Request):
    if not model_path or model_path.strip("/") in ("", "v1beta/models", "v1/models", "v1beta1/models", "models"):
        return await models(request)
    model_id = _parse_model_path(model_path)
    models_list = await get_model_catalog()
    match = next((m for m in models_list if m["id"] == model_id), None)
    if not match:
        match = {
            "id": model_id,
            "displayName": model_id,
            "description": f"{model_id} model",
            "owned_by": "google",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        }
    if _is_gemini_format_request(request):
        return _format_gemini_single_model(match)
    return _format_openai_single_model(match)


@gemini_router.api_route("/models", methods=["GET"])
async def gemini_models(request: Request):
    models_list = await get_model_catalog()
    if request.query_params.get("format", "").lower() == "openai":
        return _format_openai_models(models_list)
    return _format_gemini_models(models_list)


@gemini_router.api_route("/models/{model_path:path}", methods=["GET"])
async def gemini_get_model(model_path: str, request: Request):
    if not model_path or model_path.strip("/") in ("", "v1beta/models", "v1/models", "v1beta1/models", "models"):
        return await gemini_models(request)
    model_id = _parse_model_path(model_path)
    models_list = await get_model_catalog()
    match = next((m for m in models_list if m["id"] == model_id), None)
    if not match:
        match = {
            "id": model_id,
            "displayName": model_id,
            "description": f"{model_id} model",
            "owned_by": "google",
            "supportedGenerationMethods": ["generateContent", "countTokens"],
        }
    if request.query_params.get("format", "").lower() == "openai":
        return _format_openai_single_model(match)
    return _format_gemini_single_model(match)
