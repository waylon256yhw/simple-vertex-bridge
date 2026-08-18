from __future__ import annotations

import json
import logging
import time

import httpx
from fastapi.responses import Response, StreamingResponse

from .convert import gemini_stream_to_openai, gemini_to_openai

logger = logging.getLogger("svbridge")


async def stream_proxy(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
    log_tag: str = "",
) -> StreamingResponse:
    """Transparent streaming proxy. Preserves upstream status code and content-type."""
    start_t = time.perf_counter()

    async def _stream():
        async with client.stream(method, url, headers=headers, content=body) as resp:
            yield resp.status_code, resp.headers.get("content-type", "application/json")
            async for chunk in resp.aiter_bytes():
                yield chunk

    ait = _stream()
    status_code, media_type = await ait.__anext__()
    duration_ms = int((time.perf_counter() - start_t) * 1000)

    if log_tag:
        level = logger.info if status_code == 200 else logger.warning
        level(f"{log_tag} | status={status_code} ({duration_ms}ms)")

    async def _body():
        async for chunk in ait:
            yield chunk

    return StreamingResponse(_body(), status_code=status_code, media_type=media_type)


async def proxy_gemini_as_openai(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: bytes,
    model: str,
    is_stream: bool,
    log_tag: str = "",
) -> Response | StreamingResponse:
    """Send request to Gemini endpoint, return response in OpenAI format."""
    start_t = time.perf_counter()
    if is_stream:
        return await _stream_with_convert(client, url, headers, body, model, log_tag, start_t)

    resp = await client.post(url, headers=headers, content=body)
    duration_ms = int((time.perf_counter() - start_t) * 1000)

    if log_tag:
        level = logger.info if resp.status_code == 200 else logger.warning
        level(f"{log_tag} | status={resp.status_code} ({duration_ms}ms)")

    if resp.status_code != 200:
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type="application/json",
        )
    openai_resp = gemini_to_openai(resp.json(), model)
    return Response(
        content=json.dumps(openai_resp).encode(),
        media_type="application/json",
    )


async def _stream_with_convert(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    body: bytes,
    model: str,
    log_tag: str = "",
    start_t: float | None = None,
) -> Response | StreamingResponse:
    if start_t is None:
        start_t = time.perf_counter()

    async def _stream():
        async with client.stream("POST", url, headers=headers, content=body) as resp:
            if resp.status_code != 200:
                err_content = await resp.aread()
                yield resp.status_code, err_content
                return
            yield resp.status_code, None
            async for chunk in gemini_stream_to_openai(resp.aiter_bytes(), model):
                yield chunk

    ait = _stream()
    status_code, err_content = await ait.__anext__()
    duration_ms = int((time.perf_counter() - start_t) * 1000)

    if log_tag:
        level = logger.info if status_code == 200 else logger.warning
        level(f"{log_tag} | status={status_code} ({duration_ms}ms)")

    if status_code != 200:
        return Response(
            content=err_content,
            status_code=status_code,
            media_type="application/json",
        )

    async def _body():
        async for chunk in ait:
            yield chunk

    return StreamingResponse(_body(), status_code=200, media_type="text/event-stream")
