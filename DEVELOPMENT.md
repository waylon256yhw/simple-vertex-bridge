# Development Guide

## Background

This project is a fork of [zetaloop/simple-vertex-bridge](https://github.com/zetaloop/simple-vertex-bridge), originally a single-file 480-line Python FastAPI proxy. The refactoring added:

- Dual auth mode (Service Account + API Key Express)
- Gemini native endpoints alongside OpenAI-compatible ones
- OpenAI ↔ Gemini format conversion for API Key mode
- Configurable region with `global` location support and per-model routing
- Docker deployment
- Lowered Python requirement from 3.13 to 3.11

## Architecture

```
Client (Open WebUI, SillyTavern, etc.)
  │
  ▼
┌──────────────────────────────────────┐
│  svbridge (FastAPI + uvicorn)        │
│                                      │
│  routes.py ──► auth.py               │
│     │            │                   │
│     │     ┌──────┴──────┐            │
│     │     │ SA mode     │ API Key    │
│     │     │ (token mgmt)│ (static)   │
│     │     └──────┬──────┘            │
│     ▼            ▼                   │
│  proxy.py ◄── convert.py            │
│  (httpx h2)   (OAI ↔ Gemini)        │
└──────────────────────────────────────┘
  │
  ▼
Vertex AI API
```

## Auth Mode Decision

```
GEMINI_API_KEY set?
  ├─ Yes → AIStudioAuth
  │        - No token management
  │        - /v1/chat/completions: OpenAI → Gemini body conversion
  │        - Endpoint: generativelanguage.googleapis.com
  │
  └─ No  → VERTEX_API_KEY set?
           ├─ Yes → ApiKeyAuth (Express mode)
           │        - No token management
           │        - /v1/chat/completions: OpenAI → Gemini body conversion
           │        - Global endpoint: aiplatform.googleapis.com
           │
           └─ No  → ServiceAccountAuth
                    - Token auto-refresh (APScheduler, every 5 min)
                    - /v1/chat/completions: native passthrough (zero conversion)
                    - Regional endpoint: {loc}-aiplatform.googleapis.com
                    - global location: aiplatform.googleapis.com (no region prefix)
                    - Per-model routing: VERTEX_LOCATION_OVERRIDES (fnmatch patterns)
```

### Request Flow

**SA mode — OpenAI endpoint:**
```
Request → normalize model name → inject auth header → passthrough to Vertex OpenAI endpoint
```

**API Key mode — OpenAI endpoint:**
```
Request → openai_to_gemini() → Gemini endpoint → gemini_to_openai() → Response
```

**Gemini native endpoints (SA mode):**
```
Request → inject auth header → passthrough to Vertex Gemini endpoint
```

## Project Structure

```
svbridge/
├── main.py      # FastAPI app, lifespan, CLI, uvicorn entry
├── config.py    # AppConfig dataclass, env var loading
├── auth.py      # AuthProvider ABC + ServiceAccountAuth / ApiKeyAuth
├── routes.py    # API endpoints (/v1/chat/completions, /v1/models, Gemini native)
├── convert.py   # OpenAI ↔ Gemini format conversion
└── proxy.py     # httpx streaming proxy utilities
```

## Tech Stack

- **Python 3.11+**
- **FastAPI** — async web framework
- **uvicorn** — ASGI server
- **httpx** — async HTTP client with HTTP/2 and SOCKS support
- **google-auth** — GCP credential management

### Concurrency Design

- Single shared `httpx.AsyncClient(http2=True)` with connection limits (200 max, 50 keepalive)
- In-memory OAuth token caching with `asyncio.Lock` and non-blocking `asyncio.to_thread` refresh
- Background token refresh via async loop (no external scheduling library needed)
- Explicit timeouts: connect 10s, read 600s, write 60s, pool 30s

## Development Setup

```bash
git clone https://github.com/zetaloop/simple-vertex-bridge.git
cd simple-vertex-bridge
uv sync --all-groups
source .venv/bin/activate
```

### Run Tests & Linting

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy svbridge
```

### Run locally

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
python -m svbridge.main -b localhost -p 8086
```

### Docker

```bash
cp .env.example .env
# Edit .env
docker compose up -d --build
docker compose logs -f
```

## Format Conversion Reference

Used in API Key / AI Studio modes for `/v1/chat/completions`.

### Request (OpenAI → Gemini)

| OpenAI | Gemini |
|--------|--------|
| `messages[role=system]` | `systemInstruction.parts[].text` |
| `messages[role=user]` | `contents[]{role:"user", parts}` |
| `messages[role=assistant]` (text) | `contents[]{role:"model", parts[].text}` |
| `messages[role=assistant]` (`tool_calls`) | `contents[]{role:"model", parts[].functionCall}` |
| `messages[role=tool]` | `contents[]{role:"user", parts[].functionResponse}` |
| `content` (image_url, data URI) | `parts[].inlineData{mimeType, data}` |
| `content` (image_url, URL) | `parts[].fileData{mimeType, fileUri}` |
| `tools` (functions) | `tools[].functionDeclarations[]` |
| `tool_choice` | `toolConfig.functionCallingConfig` |
| `response_format` (JSON object/schema) | `generationConfig.responseMimeType` & `responseSchema` |
| `reasoning_effort` / `thinking_budget` | `generationConfig.thinkingConfig.thinkingBudget` |
| `max_tokens` / `max_completion_tokens` | `generationConfig.maxOutputTokens` |
| `temperature` / `top_p` / `top_k` | `generationConfig.temperature` / `topP` / `topK` |
| `presence_penalty` / `frequency_penalty` | `generationConfig.presencePenalty` / `frequencyPenalty` |
| `seed` | `generationConfig.seed` |
| `stop` | `generationConfig.stopSequences` |
| `stream: true` | `streamGenerateContent` + `?alt=sse` |

### Response (Gemini → OpenAI)

| Gemini | OpenAI |
|--------|--------|
| `candidates[0].content.parts[].text` | `choices[0].message.content` |
| `candidates[0].content.parts[].thought` | `choices[0].message.reasoning_content` |
| `candidates[0].content.parts[].functionCall` | `choices[0].message.tool_calls` |
| `usageMetadata` | `usage` |
| `finishReason: STOP` | `finish_reason: stop` |
| `finishReason: MAX_TOKENS` | `finish_reason: length` |
| `finishReason: SAFETY/RECITATION` | `finish_reason: content_filter` |

## Model Name Handling

- `/v1/models` returns bare model names for Google models (e.g. `gemini-2.5-flash`), non-Google publishers keep their prefix (e.g. `anthropic/claude-3`)
- `EXTRA_MODELS` values are returned as-is (bare names default to `owned_by: "google"`)
- Chat completion requests: bare model names are normalized to `google/gemini-2.5-flash` internally for SA mode
- `openai_to_gemini()` strips `google/` prefix before building Gemini API URLs
- Gemini native endpoints accept both `google/model-name` and bare `model-name` in the URL path

## Per-Model Location Routing

`VERTEX_LOCATION_OVERRIDES` maps model name patterns to GCP regions using `fnmatch` glob matching:

```
VERTEX_LOCATION=us-central1
VERTEX_LOCATION_OVERRIDES=gemini-3.1-*=global
```

Resolution logic in `AppConfig.resolve_location(model)`:
1. Strip publisher prefix (`google/gemini-2.5-pro` → `gemini-2.5-pro`)
2. Evaluate overrides in order, first `fnmatch` match wins
3. Fall back to `VERTEX_LOCATION`

Applied in `build_gemini_url()` and `build_openai_url()` — routes don't need to know about regions.
