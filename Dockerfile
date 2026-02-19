FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
RUN uv venv && uv pip install --no-cache -r pyproject.toml

COPY svbridge/ svbridge/

EXPOSE 8086

CMD ["/app/.venv/bin/python", "-m", "svbridge.main", "-b", "0.0.0.0"]
