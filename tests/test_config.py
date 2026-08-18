from __future__ import annotations

import os
from unittest import mock

from svbridge.config import AppConfig, load_config


def test_app_config_location_routing():
    cfg = AppConfig(
        location="us-central1",
        location_overrides=[
            ("gemini-3.1-*", "global"),
            ("claude-*", "europe-west1"),
        ],
    )
    assert cfg.resolve_location("gemini-3.1-pro-preview") == "global"
    assert cfg.resolve_location("google/gemini-3.1-pro-preview") == "global"
    assert cfg.resolve_location("anthropic/claude-3-5-sonnet") == "europe-west1"
    assert cfg.resolve_location("gemini-2.5-flash") == "us-central1"


def test_load_config_auth_mode_detection():
    with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIzaSyTestKey"}, clear=True):
        cfg = load_config()
        assert cfg.auth_mode == "aistudio"
        assert cfg.gemini_api_key == "AIzaSyTestKey"

    with mock.patch.dict(os.environ, {"VERTEX_API_KEY": "VertexKey123"}, clear=True):
        cfg = load_config()
        assert cfg.auth_mode == "api_key"
        assert cfg.api_key == "VertexKey123"

    with mock.patch.dict(os.environ, {}, clear=True):
        cfg = load_config()
        assert cfg.auth_mode == "service_account"
