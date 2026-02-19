# Development Guide

## Background

This project is a fork of [zetaloop/simple-vertex-bridge](https://github.com/zetaloop/simple-vertex-bridge), originally a single-file 480-line Python FastAPI proxy. The refactoring added:

- Dual auth mode (Service Account + API Key Express)
- Gemini native endpoints alongside OpenAI-compatible ones
- OpenAI ↔ Gemini format conversion for API Key mode
- Configurable region with `global` location support
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

### Auth Mode Decision

```
VERTEX_API_KEY set?
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
- **httpx** — async HTTP client with HTTP/2 multiplexing
- **google-auth** — GCP credential management
- **APScheduler** — background token refresh

### Concurrency Design

- Single shared `httpx.AsyncClient(http2=True)` with connection limits (200 max, 50 keepalive)
- `get_headers()` is async — blocking token refresh runs via `asyncio.to_thread()` to avoid stalling the event loop
- Token refresh uses `threading.RLock` for thread safety between the request path and APScheduler background thread
- Explicit timeouts: connect 10s, read 600s, write 60s, pool 30s

## Development Setup

```bash
git clone https://github.com/zetaloop/simple-vertex-bridge.git
cd simple-vertex-bridge
uv sync
source .venv/bin/activate
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

Only used in API Key mode for `/v1/chat/completions`.

### Request (OpenAI → Gemini)

| OpenAI | Gemini |
|--------|--------|
| `messages[role=system]` | `systemInstruction.parts[].text` |
| `messages[role=user]` | `contents[]{role:"user", parts}` |
| `messages[role=assistant]` | `contents[]{role:"model", parts}` |
| `content` (string) | `parts[].text` |
| `content` (image_url, data URI) | `parts[].inlineData{mimeType, data}` |
| `content` (image_url, URL) | `parts[].fileData{fileUri}` |
| `max_tokens` / `max_completion_tokens` | `generationConfig.maxOutputTokens` |
| `temperature` / `top_p` | `generationConfig.temperature` / `topP` |
| `stop` | `generationConfig.stopSequences` |
| `stream: true` | `streamGenerateContent` + `?alt=sse` |

### Response (Gemini → OpenAI)

| Gemini | OpenAI |
|--------|--------|
| `candidates[0].content.parts[].text` | `choices[0].message.content` |
| `usageMetadata` | `usage` |
| `finishReason: STOP` | `finish_reason: stop` |
| `finishReason: MAX_TOKENS` | `finish_reason: length` |
| `finishReason: SAFETY/RECITATION` | `finish_reason: content_filter` |

## Model Name Handling

- `EXTRA_MODELS` values without `/` are auto-prefixed with `google/` at config load time
- Chat completion requests: bare model names (e.g. `gemini-2.5-flash`) are normalized to `google/gemini-2.5-flash`
- `openai_to_gemini()` strips `google/` prefix before building Gemini API URLs
- Gemini native endpoints use bare model names in the URL path (no prefix)
