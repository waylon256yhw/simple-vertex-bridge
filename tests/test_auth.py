from __future__ import annotations

import pytest

from svbridge.auth import (
    AIStudioAuth,
    ApiKeyAuth,
    ServiceAccountAuth,
    create_auth,
)
from svbridge.config import AppConfig


def test_create_auth():
    cfg_ai = AppConfig(auth_mode="aistudio", gemini_api_key="ai-key")
    auth_ai = create_auth(cfg_ai)
    assert isinstance(auth_ai, AIStudioAuth)

    cfg_key = AppConfig(auth_mode="api_key", api_key="vertex-key")
    auth_key = create_auth(cfg_key)
    assert isinstance(auth_key, ApiKeyAuth)

    cfg_sa = AppConfig(auth_mode="service_account", project_id="my-proj")
    auth_sa = create_auth(cfg_sa)
    assert isinstance(auth_sa, ServiceAccountAuth)


def test_aistudio_urls():
    cfg = AppConfig(auth_mode="aistudio", gemini_api_key="ai-key")
    auth = AIStudioAuth(cfg)
    assert (
        auth.build_gemini_url("gemini-2.5-flash", "generateContent")
        == "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    )
    assert (
        auth.build_models_url("google") == "https://generativelanguage.googleapis.com/v1beta/models"
    )
    with pytest.raises(NotImplementedError):
        auth.build_openai_url("/chat/completions")


def test_api_key_urls():
    cfg = AppConfig(auth_mode="api_key", api_key="vertex-key", api_version="v1beta1")
    auth = ApiKeyAuth(cfg)
    url = auth.build_gemini_url("gemini-3.1-pro-preview", "generateContent")
    assert (
        "https://aiplatform.googleapis.com/v1beta1/publishers/google/models/gemini-3.1-pro-preview:generateContent"
        in url
    )
    assert "key=vertex-key" in url


def test_service_account_urls():
    cfg = AppConfig(
        auth_mode="service_account",
        project_id="test-proj",
        location="us-central1",
        location_overrides=[("gemini-3.1-*", "global")],
    )
    auth = ServiceAccountAuth(cfg)
    # Default location
    assert (
        auth.build_openai_url("/chat/completions", model="gemini-2.5-flash")
        == "https://us-central1-aiplatform.googleapis.com/v1/projects/test-proj/locations/us-central1/endpoints/openapi/chat/completions"
    )
    # Global override
    assert (
        auth.build_openai_url("/chat/completions", model="gemini-3.1-pro-preview")
        == "https://aiplatform.googleapis.com/v1/projects/test-proj/locations/global/endpoints/openapi/chat/completions"
    )
