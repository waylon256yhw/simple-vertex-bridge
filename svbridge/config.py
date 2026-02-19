from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Literal

CONFIG_FILE = "svbridge-config.json"


@dataclass
class AppConfig:
    auth_mode: Literal["service_account", "api_key"] = "service_account"
    # Service account mode
    project_id: str | None = None
    location: str = "us-central1"
    # API key mode
    api_key: str | None = None
    # Shared
    proxy_key: str = ""
    port: int = 8086
    bind: str = "localhost"
    auto_refresh: bool = True
    filter_model_names: bool = True
    publishers: list[str] = field(default_factory=lambda: ["google", "anthropic", "meta"])
    extra_models: list[str] = field(default_factory=list)
    model_names_filter: tuple[str, ...] = (
        "google/gemini-",
        "anthropic/claude-",
        "meta/llama",
    )
    # Token persistence (SA mode only)
    access_token: str | None = None
    token_expiry: str | None = None


def load_config() -> AppConfig:
    api_key = os.environ.get("VERTEX_API_KEY")
    auth_mode: Literal["service_account", "api_key"] = "api_key" if api_key else "service_account"

    publishers_env = os.environ.get("PUBLISHERS", "")
    publishers = [p.strip() for p in publishers_env.split(",") if p.strip()] or ["google", "anthropic", "meta"]

    extra_env = os.environ.get("EXTRA_MODELS", "")
    extra_models = [m.strip() for m in extra_env.split(",") if m.strip()]

    cfg = AppConfig(
        auth_mode=auth_mode,
        location=os.environ.get("VERTEX_LOCATION", "us-central1"),
        api_key=api_key,
        proxy_key=os.environ.get("PROXY_KEY", ""),
        port=int(os.environ.get("PORT", "8086")),
        bind=os.environ.get("BIND", "localhost"),
        auto_refresh=os.environ.get("AUTO_REFRESH", "true").lower() != "false",
        filter_model_names=os.environ.get("FILTER_MODEL_NAMES", "true").lower() != "false",
        publishers=publishers,
        extra_models=extra_models,
    )

    # Load persisted token from config file (SA mode)
    if auth_mode == "service_account" and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            cfg.access_token = saved.get("access_token")
            cfg.token_expiry = saved.get("token_expiry")
            if saved.get("key"):
                cfg.proxy_key = cfg.proxy_key or saved["key"]
        except (json.JSONDecodeError, OSError):
            pass

    return cfg


def save_token(cfg: AppConfig) -> None:
    data = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    data["access_token"] = cfg.access_token
    data["token_expiry"] = cfg.token_expiry
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)
