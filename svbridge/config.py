from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import Literal


@dataclass
class AppConfig:
    auth_mode: Literal["service_account", "api_key", "aistudio"] = "service_account"
    # Service account mode
    project_id: str | None = None
    location: str = "us-central1"
    api_version: str = "v1"
    location_overrides: list[tuple[str, str]] = field(default_factory=list)
    # API key mode (Vertex)
    api_key: str | None = None
    # AI Studio mode
    gemini_api_key: str | None = None
    # Shared
    proxy_key: str = ""
    port: int = 8086
    bind: str = "localhost"
    auto_refresh: bool = True
    filter_model_names: bool = True
    publishers: list[str] = field(default_factory=lambda: ["google", "anthropic", "meta"])
    extra_models: list[str] = field(default_factory=list)
    model_names_filter: tuple[str, ...] = (
        "gemini-",
        "anthropic/claude-",
        "meta/llama",
    )

    def resolve_location(self, model: str) -> str:
        bare = model.split("/")[-1] if "/" in model else model
        for pattern, loc in self.location_overrides:
            if fnmatch(bare, pattern):
                return loc
        return self.location


def load_config() -> AppConfig:
    api_key = os.environ.get("VERTEX_API_KEY") or None
    gemini_api_key = os.environ.get("GEMINI_API_KEY") or None
    if gemini_api_key:
        auth_mode: Literal["service_account", "api_key", "aistudio"] = "aistudio"
    elif api_key:
        auth_mode = "api_key"
    else:
        auth_mode = "service_account"

    publishers_env = os.environ.get("PUBLISHERS", "")
    publishers = [p.strip() for p in publishers_env.split(",") if p.strip()] or [
        "google",
        "anthropic",
        "meta",
    ]

    extra_env = os.environ.get("EXTRA_MODELS", "")
    extra_models = [m.strip() for m in extra_env.split(",") if m.strip()]
    extra_models = list(extra_models)

    overrides_env = os.environ.get("VERTEX_LOCATION_OVERRIDES", "")
    location_overrides = []
    for entry in overrides_env.split(","):
        entry = entry.strip()
        if "=" in entry:
            pattern, loc = entry.split("=", 1)
            if pattern.strip() and loc.strip():
                location_overrides.append((pattern.strip(), loc.strip()))

    return AppConfig(
        auth_mode=auth_mode,
        project_id=os.environ.get("VERTEX_PROJECT_ID") or None,
        location=os.environ.get("VERTEX_LOCATION", "us-central1"),
        api_version=os.environ.get("VERTEX_API_VERSION", "v1"),
        location_overrides=location_overrides,
        api_key=api_key,
        gemini_api_key=gemini_api_key,
        proxy_key=os.environ.get("PROXY_KEY", ""),
        port=int(os.environ.get("PORT", "8086")),
        bind=os.environ.get("BIND", "localhost"),
        auto_refresh=os.environ.get("AUTO_REFRESH", "true").lower() != "false",
        filter_model_names=os.environ.get("FILTER_MODEL_NAMES", "true").lower() != "false",
        publishers=publishers,
        extra_models=extra_models,
    )
