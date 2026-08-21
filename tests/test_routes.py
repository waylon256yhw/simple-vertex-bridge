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
    monkeypatch.setenv("EXTRA_MODELS", "gemini-2.5-flash,anthropic/claude-3-5-sonnet")
    cfg = AppConfig(
        auth_mode="aistudio",
        gemini_api_key="test-key",
        proxy_key="secret-123",
        extra_models=["gemini-2.5-flash", "anthropic/claude-3-5-sonnet"],
    )
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
    resp = client.get("/v1/models", headers={"x-goog-api-key": "secret-123"})
    assert resp.status_code == 200

    # Valid ?key= query parameter
    resp = client.get("/v1/models?key=secret-123")
    assert resp.status_code == 200


def test_invalid_json_body_returns_400(client):
    resp = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer secret-123", "Content-Type": "application/json"},
        content="not valid json",
    )
    assert resp.status_code == 400
    assert "Invalid JSON" in resp.json()["detail"]


def test_openai_models_endpoint(client):
    resp = client.get("/v1/models", headers={"Authorization": "Bearer secret-123"})
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("object") == "list"
    assert "data" in data
    ids = [m["id"] for m in data["data"]]
    assert "gemini-2.5-flash" in ids
    assert "anthropic/claude-3-5-sonnet" in ids


def test_gemini_models_v1beta_endpoint(client):
    resp = client.get("/v1beta/models?key=secret-123")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    names = [m["name"] for m in data["models"]]
    assert "models/gemini-2.5-flash" in names
    # Verify Gemini model fields
    flash_model = next(m for m in data["models"] if m["name"] == "models/gemini-2.5-flash")
    assert "supportedGenerationMethods" in flash_model
    assert "generateContent" in flash_model["supportedGenerationMethods"]


def test_gemini_models_v1beta1_and_root(client):
    resp1 = client.get("/v1beta1/models?key=secret-123")
    assert resp1.status_code == 200
    assert "models" in resp1.json()

    resp2 = client.get("/models?key=secret-123")
    assert resp2.status_code == 200
    assert "models" in resp2.json()


def test_models_format_auto_detection(client):
    # GET /v1/models with x-goog-api-key header returns Gemini format
    resp = client.get("/v1/models", headers={"x-goog-api-key": "secret-123"})
    assert resp.status_code == 200
    assert "models" in resp.json()

    # GET /v1/models with ?key= returns Gemini format
    resp = client.get("/v1/models?key=secret-123")
    assert resp.status_code == 200
    assert "models" in resp.json()

    # GET /v1/models with explicit format=gemini and Bearer token
    resp = client.get(
        "/v1/models?format=gemini", headers={"Authorization": "Bearer secret-123"}
    )
    assert resp.status_code == 200
    assert "models" in resp.json()

    # GET /v1beta/models with explicit format=openai
    resp = client.get("/v1beta/models?key=secret-123&format=openai")
    assert resp.status_code == 200
    assert resp.json().get("object") == "list"


def test_get_single_model_endpoints(client):
    # Gemini get model
    resp = client.get("/v1beta/models/gemini-2.5-flash?key=secret-123")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "models/gemini-2.5-flash"
    assert "supportedGenerationMethods" in data

    # Gemini get model with models/ prefix
    resp = client.get("/v1beta/models/models/gemini-2.5-flash?key=secret-123")
    assert resp.status_code == 200
    assert resp.json()["name"] == "models/gemini-2.5-flash"

    # OpenAI get model
    resp = client.get(
        "/v1/models/gemini-2.5-flash", headers={"Authorization": "Bearer secret-123"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "gemini-2.5-flash"
    assert data["object"] == "model"


def test_parse_model_path_helper():
    from svbridge.routes import _parse_model_path

    assert _parse_model_path("gemini-2.5-flash") == "gemini-2.5-flash"
    assert _parse_model_path("models/gemini-2.5-flash") == "gemini-2.5-flash"
    assert _parse_model_path("google/gemini-2.5-flash") == "gemini-2.5-flash"
    assert _parse_model_path("models/google/gemini-2.5-flash") == "gemini-2.5-flash"
    assert (
        _parse_model_path("publishers/google/models/gemini-2.5-flash")
        == "gemini-2.5-flash"
    )
    assert (
        _parse_model_path("publishers/anthropic/models/claude-3-5-sonnet")
        == "anthropic/claude-3-5-sonnet"
    )
    assert _parse_model_path("anthropic/claude-3-5-sonnet") == "anthropic/claude-3-5-sonnet"

