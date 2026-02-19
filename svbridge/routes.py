from __future__ import annotations

import asyncio
import json
import logging

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


async def verify_token(authorization: str | None = Header(None)) -> None:
    if not app_config.proxy_key:
        return
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(status_code=401, detail="Invalid Authorization format")
    if parts[1] != app_config.proxy_key:
        raise HTTPException(status_code=401, detail="Invalid token")


router = APIRouter(prefix="/v1", dependencies=[Depends(verify_token)])


def _normalize_model(model: str) -> str:
    """Auto-prefix 'google/' if model name has no publisher prefix."""
    if "/" not in model:
        return f"google/{model}"
    return model


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
        url = auth.build_openai_url("/chat/completions")
        if request.url.query:
            url += f"?{request.url.query}"
        headers = _proxy_headers(request, await auth.get_headers())
        raw = await request.json()
        raw["model"] = _normalize_model(raw.get("model", ""))
        body = json.dumps(raw).encode()
        return await stream_proxy(http_client, request.method, url, headers, body)

    # API key mode: convert OpenAI -> Gemini -> OpenAI
    body = await request.json()
    model, gemini_body, is_stream = openai_to_gemini(body)

    method = "streamGenerateContent" if is_stream else "generateContent"
    url = auth.build_gemini_url(model, method)
    if is_stream:
        url += "&alt=sse" if "?" in url else "?alt=sse"

    headers = {"Content-Type": "application/json"}
    payload = json.dumps(gemini_body).encode()

    return await proxy_gemini_as_openai(
        http_client, url, headers, payload, model, is_stream
    )


def _strip_publisher(model: str) -> str:
    """Strip publisher prefix (e.g. 'google/gemini-2.0-flash' -> 'gemini-2.0-flash')."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


# --- Gemini native endpoints ---


@router.api_route("/models/{model}:generateContent", methods=["POST"])
async def generate_content(model: str, request: Request):
    model = _strip_publisher(model)
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


@router.api_route("/models/{model}:streamGenerateContent", methods=["POST"])
async def stream_generate_content(model: str, request: Request):
    model = _strip_publisher(model)
    logger.info(f"[Proxy] POST models/{model}:streamGenerateContent")
    url = auth.build_gemini_url(model, "streamGenerateContent")
    query = request.url.query
    if query:
        url += f"&{query}" if "?" in url else f"?{query}"
    headers = _proxy_headers(request, await auth.get_headers())
    headers["Content-Type"] = "application/json"
    body = await request.body()
    return await stream_proxy(http_client, "POST", url, headers, body)


# --- /v1beta prefix for standard Gemini API compatibility ---
# Also handle {publisher}/{model} paths (e.g. google/gemini-2.0-flash)

v1beta_router = APIRouter(prefix="/v1beta", dependencies=[Depends(verify_token)])


@v1beta_router.api_route("/models/{model}:generateContent", methods=["POST"])
async def beta_generate_content(model: str, request: Request):
    return await generate_content(model, request)


@v1beta_router.api_route("/models/{model}:streamGenerateContent", methods=["POST"])
async def beta_stream_generate_content(model: str, request: Request):
    return await stream_generate_content(model, request)


@v1beta_router.api_route("/models/{publisher}/{model}:generateContent", methods=["POST"])
async def beta_pub_generate_content(publisher: str, model: str, request: Request):
    return await generate_content(model, request)


@v1beta_router.api_route("/models/{publisher}/{model}:streamGenerateContent", methods=["POST"])
async def beta_pub_stream_generate_content(publisher: str, model: str, request: Request):
    return await stream_generate_content(model, request)


# Also add publisher routes to /v1
@router.api_route("/models/{publisher}/{model}:generateContent", methods=["POST"])
async def pub_generate_content(publisher: str, model: str, request: Request):
    return await generate_content(model, request)


@router.api_route("/models/{publisher}/{model}:streamGenerateContent", methods=["POST"])
async def pub_stream_generate_content(publisher: str, model: str, request: Request):
    return await stream_generate_content(model, request)


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

    tasks = [_fetch(pub) for pub in app_config.publishers]
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
