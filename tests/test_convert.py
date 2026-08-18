from __future__ import annotations

import json

import pytest

from svbridge.convert import (
    _detect_mime_type,
    gemini_stream_to_openai,
    gemini_to_openai,
    openai_to_gemini,
)


def test_openai_to_gemini_basic_messages():
    body = {
        "model": "google/gemini-2.5-flash",
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "Hello!"},
            {"role": "assistant", "content": "Hi there! How can I help you today?"},
            {"role": "user", "content": "Write a python function."},
        ],
        "temperature": 0.7,
        "max_tokens": 1024,
        "top_p": 0.9,
        "top_k": 40,
        "presence_penalty": 0.5,
        "frequency_penalty": 0.3,
        "seed": 42,
        "stop": ["END"],
    }
    model, gemini_body, is_stream = openai_to_gemini(body)

    assert model == "gemini-2.5-flash"
    assert is_stream is False
    assert gemini_body["systemInstruction"] == {
        "parts": [{"text": "You are a helpful coding assistant."}]
    }
    assert len(gemini_body["contents"]) == 3
    assert gemini_body["contents"][0]["role"] == "user"
    assert gemini_body["contents"][0]["parts"] == [{"text": "Hello!"}]
    assert gemini_body["contents"][1]["role"] == "model"
    assert gemini_body["contents"][1]["parts"] == [{"text": "Hi there! How can I help you today?"}]
    assert gemini_body["contents"][2]["role"] == "user"
    assert gemini_body["contents"][2]["parts"] == [{"text": "Write a python function."}]

    gen_config = gemini_body["generationConfig"]
    assert gen_config["temperature"] == 0.7
    assert gen_config["maxOutputTokens"] == 1024
    assert gen_config["topP"] == 0.9
    assert gen_config["topK"] == 40
    assert gen_config["presencePenalty"] == 0.5
    assert gen_config["frequencyPenalty"] == 0.3
    assert gen_config["seed"] == 42
    assert gen_config["stopSequences"] == ["END"]


def test_openai_to_gemini_tools_and_choice():
    body = {
        "model": "gemini-2.5-pro",
        "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "get_current_weather",
                    "description": "Get current weather in a given city",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "location": {"type": "string", "description": "City name"},
                        },
                        "required": ["location"],
                    },
                },
            }
        ],
        "tool_choice": "required",
    }
    model, gemini_body, _ = openai_to_gemini(body)

    assert model == "gemini-2.5-pro"
    assert "tools" in gemini_body
    assert gemini_body["tools"][0]["functionDeclarations"][0]["name"] == "get_current_weather"
    assert gemini_body["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"


def test_openai_to_gemini_tool_messages():
    body = {
        "model": "gemini-2.5-pro",
        "messages": [
            {"role": "user", "content": "What's the weather in Paris?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc123",
                        "type": "function",
                        "function": {
                            "name": "get_current_weather",
                            "arguments": '{"location": "Paris"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc123",
                "name": "get_current_weather",
                "content": '{"temperature": 22, "condition": "Sunny"}',
            },
        ],
    }
    _, gemini_body, _ = openai_to_gemini(body)

    contents = gemini_body["contents"]
    assert len(contents) == 3
    # Assistant turn with functionCall
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "get_current_weather"
    assert contents[1]["parts"][0]["functionCall"]["args"] == {"location": "Paris"}

    # Tool result turn with functionResponse
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "get_current_weather"
    assert contents[2]["parts"][0]["functionResponse"]["response"] == {
        "temperature": 22,
        "condition": "Sunny",
    }


def test_openai_to_gemini_structured_outputs():
    # JSON Object mode
    body_obj = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Generate a user"}],
        "response_format": {"type": "json_object"},
    }
    _, gemini_body_obj, _ = openai_to_gemini(body_obj)
    assert gemini_body_obj["generationConfig"]["responseMimeType"] == "application/json"

    # JSON Schema mode
    schema = {
        "type": "object",
        "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        "required": ["name", "age"],
    }
    body_schema = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Generate a user"}],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "user_schema", "schema": schema},
        },
    }
    _, gemini_body_schema, _ = openai_to_gemini(body_schema)
    assert gemini_body_schema["generationConfig"]["responseMimeType"] == "application/json"
    assert gemini_body_schema["generationConfig"]["responseSchema"] == schema


def test_openai_to_gemini_thinking_config():
    # reasoning_effort
    for effort, expected_budget in [("low", 1024), ("medium", 8192), ("high", 24576), ("none", 0)]:
        body = {
            "model": "gemini-2.5-flash",
            "messages": [{"role": "user", "content": "Solve math puzzle"}],
            "reasoning_effort": effort,
        }
        _, gemini_body, _ = openai_to_gemini(body)
        assert (
            gemini_body["generationConfig"]["thinkingConfig"]["thinkingBudget"] == expected_budget
        )

    # explicit thinking budget
    body_budget = {
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "Solve math puzzle"}],
        "thinking_budget": 5000,
    }
    _, gemini_body_budget, _ = openai_to_gemini(body_budget)
    assert gemini_body_budget["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 5000


def test_openai_to_gemini_multimodal_images():
    body = {
        "model": "gemini-2.5-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this image:"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="},
                    },
                    {"type": "image_url", "image_url": "https://example.com/photo.jpg"},
                ],
            }
        ],
    }
    _, gemini_body, _ = openai_to_gemini(body)
    parts = gemini_body["contents"][0]["parts"]
    assert len(parts) == 3
    assert parts[0]["text"] == "Describe this image:"
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert parts[1]["inlineData"]["data"] == "iVBORw0KGgo="
    assert parts[2]["fileData"]["mimeType"] == "image/jpeg"
    assert parts[2]["fileData"]["fileUri"] == "https://example.com/photo.jpg"


def test_gemini_to_openai_basic():
    gemini_resp = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hello world!"}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 5,
            "totalTokenCount": 15,
        },
    }
    resp = gemini_to_openai(gemini_resp, "gemini-2.5-flash")
    assert resp["model"] == "gemini-2.5-flash"
    assert resp["object"] == "chat.completion"
    assert resp["choices"][0]["message"]["content"] == "Hello world!"
    assert resp["choices"][0]["message"]["role"] == "assistant"
    assert resp["choices"][0]["finish_reason"] == "stop"
    assert resp["usage"]["prompt_tokens"] == 10
    assert resp["usage"]["completion_tokens"] == 5
    assert resp["usage"]["total_tokens"] == 15


def test_gemini_to_openai_thinking_and_tool_calls():
    gemini_resp = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"thought": True, "text": "I should call the weather tool."},
                        {
                            "functionCall": {
                                "name": "get_current_weather",
                                "args": {"location": "London"},
                            }
                        },
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }
    resp = gemini_to_openai(gemini_resp, "gemini-2.5-pro")
    choice = resp["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["reasoning_content"] == "I should call the weather tool."
    assert len(choice["message"]["tool_calls"]) == 1
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "get_current_weather"
    assert json.loads(choice["message"]["tool_calls"][0]["function"]["arguments"]) == {
        "location": "London"
    }


@pytest.mark.asyncio
async def test_gemini_stream_to_openai():
    async def mock_stream():
        c1 = {
            "candidates": [
                {"index": 0, "content": {"parts": [{"thought": True, "text": "Thinking..."}]}}
            ]
        }
        c2 = {
            "candidates": [
                {"index": 0, "content": {"parts": [{"text": "Answer: 42"}]}, "finishReason": "STOP"}
            ],
            "usageMetadata": {
                "promptTokenCount": 5,
                "candidatesTokenCount": 10,
                "totalTokenCount": 15,
            },
        }
        yield f"data: {json.dumps(c1)}\n\n".encode()
        yield f"data: {json.dumps(c2)}\n\n".encode()

    chunks = []
    async for raw_chunk in gemini_stream_to_openai(mock_stream(), "gemini-2.5-flash"):
        chunks.append(raw_chunk.decode())

    assert len(chunks) == 3  # chunk 1, chunk 2, [DONE]
    assert "data: [DONE]" in chunks[2]

    c1_obj = json.loads(chunks[0].replace("data: ", ""))
    assert c1_obj["choices"][0]["delta"]["role"] == "assistant"
    assert c1_obj["choices"][0]["delta"]["reasoning_content"] == "Thinking..."

    c2_obj = json.loads(chunks[1].replace("data: ", ""))
    assert c2_obj["choices"][0]["delta"]["content"] == "Answer: 42"
    assert c2_obj["choices"][0]["finish_reason"] == "stop"
    assert c2_obj["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_gemini_stream_to_openai_error_handling():
    async def mock_error_stream():
        err = {"error": {"code": 429, "message": "Resource exhausted"}}
        yield f"data: {json.dumps(err)}\n\n".encode()

    chunks = []
    async for raw_chunk in gemini_stream_to_openai(mock_error_stream(), "gemini-2.5-flash"):
        chunks.append(raw_chunk.decode())

    assert len(chunks) == 2
    err_obj = json.loads(chunks[0].replace("data: ", ""))
    assert err_obj["error"]["code"] == 429
    assert err_obj["error"]["message"] == "Resource exhausted"
    assert "data: [DONE]" in chunks[1]


def test_detect_mime_type():
    assert _detect_mime_type("http://example.com/img.png") == "image/png"
    assert _detect_mime_type("http://example.com/img.JPG?v=1") == "image/jpeg"
    assert _detect_mime_type("http://example.com/doc.pdf") == "application/pdf"
    assert _detect_mime_type("http://example.com/other") == "image/jpeg"
