# Simple Vertex Bridge

A lightweight Vertex AI reverse proxy with dual auth mode and dual API format.

Based on [zetaloop/simple-vertex-bridge](https://github.com/zetaloop/simple-vertex-bridge), with the following improvements:

- **Dual auth mode**: Service Account JSON + API Key (Express)
- **Dual API format**: OpenAI-compatible + Gemini native endpoints
- **Configurable region**: `VERTEX_LOCATION` env var (no longer hardcoded)
- **Docker support**: Dockerfile + docker-compose
- **Python 3.11+**: Lowered from 3.13

[[中文]](README.zh.md)

## Auth Modes

| Mode | Trigger | Endpoints | Token Management |
|------|---------|-----------|------------------|
| **Service Account** | `GOOGLE_APPLICATION_CREDENTIALS` set | OpenAI (native passthrough) + Gemini | Auto refresh every 5 min |
| **API Key (Express)** | `VERTEX_API_KEY` set | OpenAI (auto-converted to Gemini) + Gemini | None needed |

### Service Account Mode

Uses a Google Cloud service account JSON key. Requests to the OpenAI-compatible endpoint are passed through natively to Vertex AI's OpenAI endpoint — zero conversion.

```bash
# Via gcloud CLI
gcloud auth application-default login

# Or via service account key file
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json
export VERTEX_LOCATION=us-central1  # optional, default us-central1
```

The service account needs the following IAM roles:

| Role | Purpose |
|------|---------|
| **Vertex AI User** (`roles/aiplatform.user`) | Call model endpoints (chat, generate) |
| **Service Usage Consumer** (`roles/serviceusage.serviceUsageConsumer`) | List models via `/v1/models` |

```bash
SA=your-sa@project.iam.gserviceaccount.com
PROJECT=your-project-id

gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/aiplatform.user"
gcloud projects add-iam-policy-binding $PROJECT \
  --member="serviceAccount:$SA" --role="roles/serviceusage.serviceUsageConsumer"
```

### API Key Mode (Express)

Uses a Google Cloud API key. Since Vertex AI's Express endpoint doesn't support the OpenAI format with API key auth, requests to `/v1/chat/completions` are automatically converted between OpenAI and Gemini formats.

```bash
export VERTEX_API_KEY=your-google-cloud-api-key
```

## API Endpoints

All endpoints accept an optional `Authorization: Bearer <PROXY_KEY>` header when `PROXY_KEY` is configured.

### OpenAI-compatible

**`POST /v1/chat/completions`**

Standard OpenAI chat completion format. Supports streaming.

```bash
curl http://localhost:8086/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_PROXY_KEY" \
  -d '{
    "model": "google/gemini-2.5-flash",
    "messages": [{"role": "user", "content": "Hello"}],
    "stream": false
  }'
```

Supported parameters: `model`, `messages`, `stream`, `temperature`, `top_p`, `max_tokens`, `max_completion_tokens`, `stop`, `n`.

**`GET /v1/models`**

Returns available models in OpenAI format.

### Gemini Native (Service Account mode)

**`POST /v1/models/{model}:generateContent`**
**`POST /v1/models/{model}:streamGenerateContent`**

Direct Gemini API passthrough — body is forwarded as-is with auth injected.

```bash
curl http://localhost:8086/v1/models/gemini-2.5-flash:generateContent \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_PROXY_KEY" \
  -d '{"contents":[{"parts":[{"text":"Hello"}]}]}'
```

## Quick Start

### Docker (recommended)

1. Create `.env` from the example:
```bash
cp .env.example .env
```

2. Edit `.env`:
```bash
# Service Account mode
GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json
VERTEX_LOCATION=us-central1
PROXY_KEY=your-secret-key

# Or API Key mode
# VERTEX_API_KEY=your-google-cloud-api-key
# PROXY_KEY=your-secret-key
```

3. If using Service Account mode, place your SA JSON file in the project directory and update `docker-compose.yml` volume mount accordingly.

4. Start:
```bash
docker compose up -d
```

### Direct

```bash
# Install and run from PyPI
uvx simple-vertex-bridge -b 0.0.0.0 -k your-secret-key

# Or from source
git clone https://github.com/zetaloop/simple-vertex-bridge.git
cd simple-vertex-bridge
uv sync && source .venv/bin/activate
python -m svbridge.main -b 0.0.0.0 -k your-secret-key
```

## Configuration

### CLI Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `-p`, `--port` | `8086` | Port to listen on |
| `-b`, `--bind` | `localhost` | Bind address |
| `-k`, `--key` | *(any)* | Proxy authentication key |
| `--auto-refresh` / `--no-auto-refresh` | on | Background token refresh (SA mode) |
| `--filter-model-names` / `--no-filter-model-names` | on | Filter common model names in `/v1/models` |

CLI arguments override environment variables.

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VERTEX_API_KEY` | — | Google Cloud API key (triggers Express mode) |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | Path to service account JSON |
| `VERTEX_LOCATION` | `us-central1` | Google Cloud region (SA mode only) |
| `PROXY_KEY` | *(any)* | Bearer token for proxy auth |
| `PORT` | `8086` | Server port |
| `BIND` | `localhost` | Bind address |
| `PUBLISHERS` | `google,anthropic,meta` | Publisher list for model fetching |
| `EXTRA_MODELS` | — | Extra model IDs to append to `/v1/models` |

## License

The Unlicense.
