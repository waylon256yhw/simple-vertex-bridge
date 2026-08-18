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
    model = model.removeprefix("google/")

    is_stream = bool(body.get("stream", False))

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
    if (v := body.get("top_k")) is not None:
        gen_config["topK"] = v
    if (v := body.get("presence_penalty")) is not None:
        gen_config["presencePenalty"] = v
    if (v := body.get("frequency_penalty")) is not None:
        gen_config["frequencyPenalty"] = v
    if (v := body.get("seed")) is not None:
        gen_config["seed"] = v

    stop = body.get("stop")
    if stop is not None:
        gen_config["stopSequences"] = [stop] if isinstance(stop, str) else stop
    n = body.get("n")
    if n is not None and n > 1:
        gen_config["candidateCount"] = n

    # Structured Outputs / response_format
    if isinstance(response_format := body.get("response_format"), dict):
        fmt_type = response_format.get("type")
        if fmt_type == "json_object":
            gen_config["responseMimeType"] = "application/json"
        elif fmt_type == "json_schema":
            gen_config["responseMimeType"] = "application/json"
            if schema := response_format.get("json_schema", {}).get("schema"):
                gen_config["responseSchema"] = schema
        elif fmt_type == "text":
            gen_config["responseMimeType"] = "text/plain"

    # Thinking / Reasoning configuration (Gemini 2.0 / 3.x)
    thinking_config = _build_thinking_config(body)
    if thinking_config is not None:
        gen_config["thinkingConfig"] = thinking_config

    gemini_body: dict = {"contents": contents}
    if system_instruction:
        gemini_body["systemInstruction"] = system_instruction
    if gen_config:
        gemini_body["generationConfig"] = gen_config

    # Tool / Function Calling
    tools, tool_config = _convert_tools(body.get("tools"), body.get("tool_choice"))
    if tools:
        gemini_body["tools"] = tools
    if tool_config:
        gemini_body["toolConfig"] = tool_config

    return model, gemini_body, is_stream


def _build_thinking_config(body: dict) -> dict | None:
    """Extract reasoning / thinking settings from request body."""
    # Check explicit thinking config
    if "thinkingConfig" in body:
        return body["thinkingConfig"]
    if isinstance(extra := body.get("extra_body"), dict) and "thinkingConfig" in extra:
        return extra["thinkingConfig"]

    # Check thinking budget parameters
    if (budget := body.get("thinking_budget")) is not None:
        return {"thinkingBudget": int(budget)}
    if isinstance(thinking := body.get("thinking"), dict) and "budget" in thinking:
        return {"thinkingBudget": int(thinking["budget"])}

    # Map OpenAI reasoning_effort to thinkingBudget
    if reasoning_effort := body.get("reasoning_effort"):
        effort_map = {
            "none": 0,
            "low": 1024,
            "medium": 8192,
            "high": 24576,
        }
        if str(reasoning_effort).lower() in effort_map:
            return {"thinkingBudget": effort_map[str(reasoning_effort).lower()]}

    return None


def _convert_tools(
    tools: list[dict] | None, tool_choice: str | dict | None
) -> tuple[list[dict] | None, dict | None]:
    """Convert OpenAI tools and tool_choice to Gemini format."""
    if not tools:
        return None, None

    declarations = []
    for tool in tools:
        if tool.get("type") == "function" and (fn := tool.get("function")):
            decl = {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
            }
            if params := fn.get("parameters"):
                decl["parameters"] = params
            declarations.append(decl)

    if not declarations:
        return None, None

    gemini_tools = [{"functionDeclarations": declarations}]
    gemini_tool_config = None

    if tool_choice:
        if isinstance(tool_choice, str):
            choice_upper = tool_choice.upper()
            if choice_upper == "AUTO":
                gemini_tool_config = {"functionCallingConfig": {"mode": "AUTO"}}
            elif choice_upper == "NONE":
                gemini_tool_config = {"functionCallingConfig": {"mode": "NONE"}}
            elif choice_upper == "REQUIRED":
                gemini_tool_config = {"functionCallingConfig": {"mode": "ANY"}}
        elif isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            fn_name = tool_choice.get("function", {}).get("name")
            if fn_name:
                config_dict: dict = {
                    "mode": "ANY",
                    "allowedFunctionNames": [fn_name],
                }
                gemini_tool_config = {"functionCallingConfig": config_dict}

    return gemini_tools, gemini_tool_config


def gemini_to_openai(gemini_resp: dict, model: str) -> dict:
    """Convert Gemini generateContent response to OpenAI chat completion."""
    candidates = gemini_resp.get("candidates", [])
    usage_meta = gemini_resp.get("usageMetadata", {})

    choices = []
    for i, candidate in enumerate(candidates):
        parts = candidate.get("content", {}).get("parts", [])
        text_parts: list[str] = []
        thought_parts: list[str] = []
        tool_calls: list[dict] = []

        for p in parts:
            if p.get("thought"):
                thought_parts.append(p.get("text", ""))
            elif "text" in p:
                text_parts.append(p.get("text", ""))
            elif "functionCall" in p:
                fc = p["functionCall"]
                tool_calls.append(
                    {
                        "id": f"call_{uuid.uuid4().hex[:12]}",
                        "type": "function",
                        "function": {
                            "name": fc.get("name", ""),
                            "arguments": json.dumps(fc.get("args", {})),
                        },
                    }
                )

        text = "".join(text_parts)
        message: dict = {"role": "assistant"}
        finish_reason: str | None

        if tool_calls:
            message["content"] = text or None
            message["tool_calls"] = tool_calls
            finish_reason = "tool_calls"
        else:
            message["content"] = text
            finish_reason = _map_finish_reason(candidate.get("finishReason"))

        if thought_parts:
            message["reasoning_content"] = "".join(thought_parts)

        choices.append(
            {
                "index": i,
                "message": message,
                "finish_reason": finish_reason,
            }
        )

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

            if "error" in gemini_chunk:
                error_obj = gemini_chunk["error"]
                err_msg = (
                    error_obj.get("message", "Unknown upstream error")
                    if isinstance(error_obj, dict)
                    else str(error_obj)
                )
                err_chunk = {
                    "error": {
                        "message": err_msg,
                        "type": "upstream_error",
                        "code": error_obj.get("code", 500) if isinstance(error_obj, dict) else 500,
                    }
                }
                yield f"data: {json.dumps(err_chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"
                return

            if "promptFeedback" in gemini_chunk and "blockReason" in gemini_chunk["promptFeedback"]:
                feedback_chunk = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "content_filter",
                        }
                    ],
                }
                yield f"data: {json.dumps(feedback_chunk)}\n\n".encode()
                yield b"data: [DONE]\n\n"
                return

            for candidate in gemini_chunk.get("candidates", []):
                parts = candidate.get("content", {}).get("parts", [])
                text_parts: list[str] = []
                thought_parts: list[str] = []
                tool_calls: list[dict] = []

                for p in parts:
                    if p.get("thought"):
                        thought_parts.append(p.get("text", ""))
                    elif "text" in p:
                        text_parts.append(p.get("text", ""))
                    elif "functionCall" in p:
                        fc = p["functionCall"]
                        tool_calls.append(
                            {
                                "index": len(tool_calls),
                                "id": f"call_{uuid.uuid4().hex[:12]}",
                                "type": "function",
                                "function": {
                                    "name": fc.get("name", ""),
                                    "arguments": json.dumps(fc.get("args", {})),
                                },
                            }
                        )

                delta: dict = {}
                if first_chunk:
                    delta["role"] = "assistant"
                    first_chunk = False
                if thought_parts:
                    delta["reasoning_content"] = "".join(thought_parts)
                if text_parts:
                    delta["content"] = "".join(text_parts)
                if tool_calls:
                    delta["tool_calls"] = tool_calls

                finish = candidate.get("finishReason")
                finish_reason = "tool_calls" if tool_calls else _map_finish_reason(finish)

                chunk: dict = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [
                        {
                            "index": candidate.get("index", 0),
                            "delta": delta,
                            "finish_reason": finish_reason,
                        }
                    ],
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
    """Convert OpenAI messages to Gemini systemInstruction and contents."""
    system_parts: list[dict] = []
    contents: list[dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str):
                if content:
                    system_parts.append({"text": content})
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text" and item.get("text"):
                        system_parts.append({"text": item["text"]})
            continue

        if role in ("tool", "function"):
            fn_name = msg.get("name", "tool_result")
            raw_result = content
            if isinstance(raw_result, str):
                try:
                    res_obj = json.loads(raw_result)
                except json.JSONDecodeError:
                    res_obj = {"output": raw_result}
            else:
                res_obj = (
                    raw_result if isinstance(raw_result, dict) else {"output": str(raw_result)}
                )

            part = {
                "functionResponse": {
                    "name": fn_name,
                    "response": res_obj if isinstance(res_obj, dict) else {"output": res_obj},
                }
            }
            contents.append({"role": "user", "parts": [part]})
            continue

        gemini_role = "model" if role == "assistant" else "user"
        parts = _content_to_parts(content)

        # Handle assistant tool_calls in message
        if role == "assistant" and (tool_calls := msg.get("tool_calls")):
            for tc in tool_calls:
                if tc.get("type") == "function" and (fn := tc.get("function")):
                    args = fn.get("arguments", "{}")
                    try:
                        parsed_args = json.loads(args) if isinstance(args, str) else args
                    except json.JSONDecodeError:
                        parsed_args = {"raw_arguments": args}
                    parts.append(
                        {
                            "functionCall": {
                                "name": fn.get("name", ""),
                                "args": parsed_args
                                if isinstance(parsed_args, dict)
                                else {"value": parsed_args},
                            }
                        }
                    )

        if parts:
            contents.append({"role": gemini_role, "parts": parts})

    system_instruction = {"parts": system_parts} if system_parts else None
    return system_instruction, contents


def _detect_mime_type(url: str) -> str:
    """Infer image or document MIME type from URL extension."""
    url_lower = url.lower().split("?")[0]
    if url_lower.endswith(".png"):
        return "image/png"
    if url_lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if url_lower.endswith(".webp"):
        return "image/webp"
    if url_lower.endswith(".gif"):
        return "image/gif"
    if url_lower.endswith(".pdf"):
        return "application/pdf"
    return "image/jpeg"


def _content_to_parts(content) -> list[dict]:
    """Convert OpenAI message content to Gemini content parts."""
    if isinstance(content, str):
        return [{"text": content}] if content else []

    if not isinstance(content, list):
        return [{"text": str(content)}] if content is not None else []

    parts: list[dict] = []
    for item in content:
        t = item.get("type", "text")
        if t == "text":
            if text := item.get("text"):
                parts.append({"text": text})
        elif t == "image_url":
            url_obj = item.get("image_url", {})
            url = url_obj.get("url", "") if isinstance(url_obj, dict) else str(url_obj)
            if url.startswith("data:"):
                mime, _, b64data = url.partition(";base64,")
                mime = mime.removeprefix("data:") or "image/jpeg"
                parts.append({"inlineData": {"mimeType": mime, "data": b64data}})
            elif url:
                mime = _detect_mime_type(url)
                parts.append({"fileData": {"mimeType": mime, "fileUri": url}})
    return parts


def _map_finish_reason(reason: str | None) -> str | None:
    """Map Gemini finish reasons to OpenAI finish reasons."""
    if not reason:
        return None
    return {
        "STOP": "stop",
        "MAX_TOKENS": "length",
        "SAFETY": "content_filter",
        "RECITATION": "content_filter",
        "BLOCKLIST": "content_filter",
        "PROHIBITED_CONTENT": "content_filter",
        "SPII": "content_filter",
        "FINISH_REASON_STOP": "stop",
        "FINISH_REASON_MAX_TOKENS": "length",
        "FINISH_REASON_SAFETY": "content_filter",
        "FINISH_REASON_RECITATION": "content_filter",
        "FINISH_REASON_BLOCKLIST": "content_filter",
    }.get(reason, "stop")
