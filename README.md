# Simple Vertex Bridge

Vertex AI reverse proxy — store credentials on the server, expose OpenAI-compatible API to your frontends.

Based on [zetaloop/simple-vertex-bridge](https://github.com/zetaloop/simple-vertex-bridge). [[中文]](README.zh.md)

## Quick Start

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up -d
```

Your API is now available at `http://localhost:8086`.

## Connect Your Frontend

Use these settings in Open WebUI, SillyTavern, or any OpenAI-compatible client:

| Setting | Value |
|---------|-------|
| API Base URL | `http://your-server:8086/v1` |
| API Key | Your `PROXY_KEY` value |
| Model | Pick from the model list, or type any model name |

Model names work with or without the `google/` prefix:

```
gemini-2.5-flash          ← auto-prefixed to google/gemini-2.5-flash
google/gemini-2.5-flash   ← works as-is
```

## Configuration

Edit `.env` and restart (`docker compose up -d`).

### Authentication (pick one)

**Service Account** (recommended) — uses a JSON key file:

```bash
GOOGLE_APPLICATION_CREDENTIALS=/app/sa.json
SA_FILE=your-key.json        # filename on host, mounted into container
VERTEX_LOCATION=us-central1  # default region for all models
```

Required IAM roles for the service account:

| Role | Purpose |
|------|---------|
| `roles/aiplatform.user` | Call models |
| `roles/serviceusage.serviceUsageConsumer` | List models |

**API Key** (simpler, fewer features) — uses a Google Cloud API key:

```bash
VERTEX_API_KEY=your-api-key
```

### Server

```bash
PROXY_KEY=your-secret     # clients must send as Bearer token (empty = allow any)
PORT=8086
BIND=0.0.0.0
```

### Model List

```bash
PUBLISHERS=google                  # which publishers to fetch (default: google,anthropic,meta)
EXTRA_MODELS=gemini-3.1-pro-preview  # always show these (auto-prefixed with google/)
```

`PUBLISHERS` controls which publishers are queried for the model list. Set `PUBLISHERS=google` to show only Google models.

`EXTRA_MODELS` adds models that may not appear in the API listing (e.g. new preview models). Bare names are auto-prefixed with `google/`.

### Per-Model Region Routing

Some models are only available in certain regions (e.g. preview models require `global`). Use `VERTEX_LOCATION_OVERRIDES` to route specific models to different regions:

```bash
VERTEX_LOCATION=us-central1                      # default region
VERTEX_LOCATION_OVERRIDES=gemini-3.1-*=global     # preview models → global
```

Format: `pattern=region` pairs, comma-separated. Supports `*` and `?` wildcards ([fnmatch](https://docs.python.org/3/library/fnmatch.html)). First match wins, unmatched models use `VERTEX_LOCATION`.

### All Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `VERTEX_API_KEY` | — | API key (triggers Express mode) |
| `GOOGLE_APPLICATION_CREDENTIALS` | — | SA JSON path inside container |
| `SA_FILE` | `sa.json` | SA JSON filename on host (for Docker mount) |
| `VERTEX_LOCATION` | `us-central1` | Default region (`us-central1`, `global`, etc.) |
| `VERTEX_LOCATION_OVERRIDES` | — | Per-model region routing (`pattern=region,...`) |
| `PROXY_KEY` | *(any)* | Bearer token or `?key=` for proxy auth |
| `PORT` | `8086` | Server port |
| `BIND` | `localhost` | Bind address |
| `PUBLISHERS` | `google,anthropic,meta` | Publishers to fetch models from |
| `EXTRA_MODELS` | — | Extra models to always show |

### CLI Arguments

When running without Docker:

```bash
simple-vertex-bridge -p 8086 -b 0.0.0.0 -k your-secret
```

`-p/--port`, `-b/--bind`, `-k/--key`, `--auto-refresh/--no-auto-refresh`, `--filter-model-names/--no-filter-model-names`. CLI args override env vars.

## API Endpoints

| Endpoint | Format | Description |
|----------|--------|-------------|
| `POST /v1/chat/completions` | OpenAI | Chat completion (streaming supported) |
| `GET /v1/models` | OpenAI | List available models |
| `POST /v1/models/{model}:generateContent` | Gemini | Native Gemini (SA mode only) |
| `POST /v1/models/{model}:streamGenerateContent` | Gemini | Native Gemini streaming (SA mode only) |
| `POST /v1beta/models/{model}:*` | Gemini | Same as above, v1beta prefix |

Gemini endpoints accept `google/model-name` or bare `model-name` in the path. Auth via `Authorization: Bearer <key>` header or `?key=<key>` query parameter.

## Development

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture, tech stack, and development setup.

## License

The Unlicense.
