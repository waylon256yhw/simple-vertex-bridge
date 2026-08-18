from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from svbridge.auth import create_auth
from svbridge.config import AppConfig
from svbridge.main import app
from svbridge.routes import init as init_routes


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("PROXY_KEY", "secret-123")
    cfg = AppConfig(auth_mode="aistudio", gemini_api_key="test-key", proxy_key="secret-123")
    auth_provider = create_auth(cfg)
    http_c = httpx.AsyncClient(trust_env=False)
    init_routes(cfg, auth_provider, http_c)
    with TestClient(app) as tc:
        yield tc


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_verify_token_authorization_header(client):
    # Missing auth
    resp = client.get("/v1/models")
    assert resp.status_code == 401

    # Invalid Bearer auth
    resp = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 401

    # Valid x-goog-api-key header
    # Upstream mock not set, so it attempts to connect or fails at fetch, but verify_token passes (status != 401)
    resp = client.get("/v1/models", headers={"x-goog-api-key": "secret-123"})
    assert resp.status_code != 401

    # Valid ?key= query parameter
    resp = client.get("/v1/models?key=secret-123")
    assert resp.status_code != 401


def test_invalid_json_body_returns_400(client):
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret-123", "Content-Type": "application/json"},
        content="not valid json",
    )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["detail"]
