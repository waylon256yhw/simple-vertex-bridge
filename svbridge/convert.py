from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator


def openai_to_gemini(body: dict) -> tuple[str, dict, bool]:
    """Convert OpenAI chat completion request to Gemini generateContent request.

    Returns (model_name, gemini_body, is_stream).
    """
    model = body.get("model", "")
    if model.startswith("google/"):
        model = model[len("google/"):]

    is_stream = body.get("stream", False)

    system_instruction, contents = _convert_messages(body.get("messages", []))

    gen_config: dict = {}
    if (v := body.get("max_tokens")) is not None:
        gen_config["maxOutputTokens"] = v
    if (v := body.get("max_completion_tokens")) is not None:
        gen_config["maxOutputTokens"] = v
    if (v := body.get("temperature")) is not None:
        gen_config["temperature"] = v
    if (v := body.get("top_p")) is not None:
        gen_config["topP"] = v
    stop = body.get("stop")
    if stop is not None:
        gen_config["stopSequences"] = [stop] if isinstance(stop, str) else stop
    n = body.get("n")
    if n is not None and n > 1:
        gen_config["candidateCount"] = n

    gemini_body: dict = {"contents": contents}
    if system_instruction:
        gemini_body["systemInstruction"] = system_instruction
    if gen_config:
        gemini_body["generationConfig"] = gen_config

    return model, gemini_body, is_stream


def gemini_to_openai(gemini_resp: dict, model: str) -> dict:
    """Convert Gemini generateContent response to OpenAI chat completion."""
    candidates = gemini_resp.get("candidates", [])
    usage_meta = gemini_resp.get("usageMetadata", {})

    choices = []
    for i, candidate in enumerate(candidates):
        parts = candidate.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        choices.append({
            "index": i,
            "message": {"role": "assistant", "content": text},
            "finish_reason": _map_finish_reason(candidate.get("finishReason")),
        })

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": choices,
        "usage": {
            "prompt_tokens": usage_meta.get("promptTokenCount", 0),
            "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
            "total_tokens": usage_meta.get("totalTokenCount", 0),
        },
    }


async def gemini_stream_to_openai(
    raw_stream: AsyncIterator[bytes], model: str
) -> AsyncIterator[bytes]:
    """Parse Gemini SSE stream and yield OpenAI-format SSE chunks."""
    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())
    first_chunk = True
    buffer = b""

    async for raw_bytes in raw_stream:
        buffer += raw_bytes
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            line = line.strip()
            if not line or not line.startswith(b"data: "):
                continue
            json_str = line[6:]
            try:
                gemini_chunk = json.loads(json_str)
            except json.JSONDecodeError:
                continue

            for candidate in gemini_chunk.get("candidates", []):
                parts = candidate.get("content", {}).get("parts", [])
                text = "".join(p.get("text", "") for p in parts)
                finish = candidate.get("finishReason")

                delta: dict = {}
                if first_chunk:
                    delta["role"] = "assistant"
                    first_chunk = False
                if text:
                    delta["content"] = text

                chunk: dict = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{
                        "index": candidate.get("index", 0),
                        "delta": delta,
                        "finish_reason": _map_finish_reason(finish),
                    }],
                }

                usage_meta = gemini_chunk.get("usageMetadata")
                if usage_meta and finish:
                    chunk["usage"] = {
                        "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                        "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                        "total_tokens": usage_meta.get("totalTokenCount", 0),
                    }

                yield f"data: {json.dumps(chunk)}\n\n".encode()

    yield b"data: [DONE]\n\n"


def _convert_messages(messages: list[dict]) -> tuple[dict | None, list[dict]]:
    system_parts: list[dict] = []
    contents: list[dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str):
                system_parts.append({"text": content})
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        system_parts.append({"text": item["text"]})
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _content_to_parts(content)
        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    system_instruction = {"parts": system_parts} if system_parts else None
    return system_instruction, contents


def _content_to_parts(content) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]

    if not isinstance(content, list):
        return [{"text": str(content)}] if content else []

    parts: list[dict] = []
    for item in content:
        t = item.get("type", "text")
        if t == "text":
            parts.append({"text": item.get("text", "")})
        elif t == "image_url":
            url = item.get("image_url", {})
            if isinstance(url, dict):
                url = url.get("url", "")
            if url.startswith("data:"):
                mime, _, b64data = url.partition(";base64,")
                mime = mime.removeprefix("data:")
                parts.append({"inlineData": {"mimeType": mime, "data": b64data}})
            elif url:
                parts.append({"fileData": {"mimeType": "image/jpeg", "fileUri": url}})
    return parts


def _map_finish_reason(reason: str | None) -> str | None:
    if not reason:
        return None
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "FINISH_REASON_STOP": "stop",
        "FINISH_REASON_MAX_TOKENS": "length",
        "FINISH_REASON_SAFETY": "content_filter",
    }.get(reason)
