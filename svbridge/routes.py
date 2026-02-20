from __future__ import annotations

import asyncio
import json
import logging
import secrets
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
            raise HTTPException(status_code=401, detail="Invalid token")
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    # Fall back to ?key= query parameter for Gemini API clients
    key_param = request.query_params.get("key")
    if key_param is not None:
        if secrets.compare_digest(key_param, app_config.proxy_key) if key_param else False:
            return
        raise HTTPException(status_code=401, detail="Invalid key")
    raise HTTPException(status_code=401, detail="Missing Authorization header")


router = APIRouter(prefix="/v1", dependencies=[Depends(verify_token)])


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
        k: v for k, v in request.headers.items()
        if k.lower() not in ("host", "authorization", "content-length")
    }
    headers.update(auth_headers)
    return headers


# --- OpenAI-compatible endpoint ---


@router.api_route("/chat/completions", methods=["GET", "POST"])
async def chat_completions(request: Request):
    logger.info(f"[Proxy] {request.method} /v1/chat/completions")

    if app_config.auth_mode == "service_account":
        raw = await request.json()
        raw["model"] = _normalize_model(raw.get("model", ""))
        url = auth.build_openai_url("/chat/completions", model=raw["model"])
        qs = _forward_query(request)
        if qs:
            url += "?" + qs
        headers = _proxy_headers(request, await auth.get_headers())
        body = json.dumps(raw).encode()
        return await stream_proxy(http_client, request.method, url, headers, body)

    # API key / AI Studio mode: convert OpenAI -> Gemini -> OpenAI
    body = await request.json()
    model, gemini_body, is_stream = openai_to_gemini(body)

    method = "streamGenerateContent" if is_stream else "generateContent"
    url = auth.build_gemini_url(model, method)
    if is_stream:
        url += "&alt=sse" if "?" in url else "?alt=sse"

    headers = {"Content-Type": "application/json"}
    headers.update(await auth.get_headers())
    payload = json.dumps(gemini_body).encode()

    return await proxy_gemini_as_openai(
        http_client, url, headers, payload, model, is_stream
    )


def _parse_model_path(model_path: str) -> str:
    """Parse model path, strip publisher prefix if present."""
    parts = model_path.split("/")
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[1]
    raise HTTPException(status_code=400, detail="Invalid model path")


# --- Gemini native endpoints ---

gemini_router = APIRouter(dependencies=[Depends(verify_token)])


@gemini_router.api_route("/models/{model_path:path}:generateContent", methods=["POST"])
async def generate_content(model_path: str, request: Request):
    model = _parse_model_path(model_path)
    logger.info(f"[Proxy] POST models/{model}:generateContent")
    url = auth.build_gemini_url(model, "generateContent")
    headers = _proxy_headers(request, await auth.get_headers())
    headers["Content-Type"] = "application/json"
    body = await request.body()
    resp = await http_client.post(url, headers=headers, content=body)
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type="application/json",
    )


@gemini_router.api_route("/models/{model_path:path}:streamGenerateContent", methods=["POST"])
async def stream_generate_content(model_path: str, request: Request):
    model = _parse_model_path(model_path)
    logger.info(f"[Proxy] POST models/{model}:streamGenerateContent")
    url = auth.build_gemini_url(model, "streamGenerateContent")
    qs = _forward_query(request)
    if qs:
        url += f"&{qs}" if "?" in url else f"?{qs}"
    headers = _proxy_headers(request, await auth.get_headers())
    headers["Content-Type"] = "application/json"
    body = await request.body()
    return await stream_proxy(http_client, "POST", url, headers, body)


# --- Model listing ---


@router.api_route("/models", methods=["GET"])
async def models(request: Request):
    logger.info("[Models] Fetching model list")

    async def _fetch(publisher: str) -> list[dict]:
        url = auth.build_models_url(publisher)
        headers = {"Content-Type": "application/json"}
        auth_headers = await auth.get_headers()
        headers.update(auth_headers)
        if app_config.auth_mode == "service_account" and app_config.project_id:
            headers["x-goog-user-project"] = app_config.project_id

        for attempt in range(3):
            try:
                resp = await http_client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"[Models] {publisher}: {resp.status_code}")
                    return []
                data = resp.json()
                result = []

                # AI Studio format: {"models": [{"name": "models/gemini-..."}]}
                if "models" in data:
                    for m in data["models"]:
                        name = m.get("name", "")
                        model_id = name.removeprefix("models/")
                        result.append({
                            "id": f"google/{model_id}",
                            "object": "model",
                            "owned_by": "google",
                        })
                    return result

                # Vertex format: {"publisherModels": [...]}
                for m in data.get("publisherModels", []):
                    name = m.get("name", "")
                    parts = name.split("/")
                    if len(parts) == 4 and parts[0] == "publishers" and parts[2] == "models":
                        model_id = f"{parts[1]}/{parts[3]}"
                        result.append({
                            "id": model_id,
                            "object": "model",
                            "owned_by": parts[1],
                        })
                return result
            except httpx.RequestError as e:
                if attempt < 2:
                    logger.warning(f"[Models] {publisher} retry: {e}")
                    await asyncio.sleep(0.2)
                    continue
                logger.warning(f"[Models] {publisher} failed: {e}")
                return []

    pubs = ["google"] if app_config.auth_mode == "aistudio" else app_config.publishers
    tasks = [_fetch(pub) for pub in pubs]
    results = await asyncio.gather(*tasks)

    all_models: list[dict] = []
    for models_list in results:
        all_models.extend(models_list)

    if app_config.filter_model_names:
        all_models = [
            m for m in all_models
            if any(m["id"].startswith(prefix) for prefix in app_config.model_names_filter)
        ]

    for model_id in app_config.extra_models:
        owner = model_id.split("/")[0] if "/" in model_id else "custom"
        all_models.append({"id": model_id, "object": "model", "owned_by": owner})

    logger.info(f"[Models] Returning {len(all_models)} models")
    return {"object": "list", "data": all_models}
