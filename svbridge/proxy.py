from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
from fastapi.responses import Response, StreamingResponse

from .convert import gemini_stream_to_openai, gemini_to_openai


async def stream_proxy(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict[str, str],
    body: bytes,
) -> StreamingResponse:
    """Transparent streaming proxy. Preserves upstream status code and content-type."""

    async def _stream():
        async with client.stream(method, url, headers=headers, content=body) as resp:
            yield resp.status_code, resp.headers.get("content-type", "application/json")
            async for chunk in resp.aiter_bytes():
                yield chunk

    ait = _stream()
    status_code, media_type = await ait.__anext__()

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
) -> Response | StreamingResponse:
    """Send request to Gemini endpoint, return response in OpenAI format."""
    if is_stream:
        return await _stream_with_convert(client, url, headers, body, model)

    resp = await client.post(url, headers=headers, content=body)
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
) -> Response | StreamingResponse:
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
