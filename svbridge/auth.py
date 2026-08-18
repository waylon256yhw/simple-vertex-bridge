from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from .config import AppConfig

logger = logging.getLogger("svbridge")

TOKEN_EXPIRY_BUFFER = timedelta(minutes=10)
BACKGROUND_INTERVAL = 5  # minutes


class AuthProvider(ABC):
    @abstractmethod
    async def get_headers(self) -> dict[str, str]: ...

    @abstractmethod
    def build_openai_url(self, path: str, model: str = "") -> str: ...

    @abstractmethod
    def build_gemini_url(self, model: str, method: str) -> str: ...

    @abstractmethod
    def build_models_url(self, publisher: str) -> str: ...

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


class ServiceAccountAuth(AuthProvider):
    def __init__(self, config: AppConfig):
        self.config = config
        self._credentials = None
        self._lock = asyncio.Lock()
        self._token: str | None = None
        self._expiry: datetime | None = None
        self._bg_task: asyncio.Task | None = None

    def _sync_refresh(self) -> tuple[str | None, datetime | None]:
        from google.auth import default
        from google.auth.transport.requests import Request

        try:
            creds = self._credentials
            if creds is None:
                creds, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
                self._credentials = creds
            creds.refresh(Request())
            token = creds.token
            expiry = creds.expiry
            if expiry:
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=UTC)
                else:
                    expiry = expiry.astimezone(UTC)
            return token, expiry
        except Exception as e:  # noqa: BLE001
            logger.error(f"[Token] Failed to fetch token: {e}")
            return None, None

    def _is_valid(self) -> bool:
        if not self._token or not self._expiry:
            return False
        return datetime.now(UTC) + TOKEN_EXPIRY_BUFFER < self._expiry

    async def _refresh_token(self) -> bool:
        token, expiry = await asyncio.to_thread(self._sync_refresh)
        if token and expiry:
            self._token = token
            self._expiry = expiry
            logger.info("[Token] Token refreshed")
            return True
        logger.error("[Token] Token refresh failed")
        return False

    async def get_headers(self) -> dict[str, str]:
        if not self._is_valid():
            async with self._lock:
                if not self._is_valid():
                    await self._refresh_token()
        if not self._token:
            raise RuntimeError("No valid access token available")
        headers = {"Authorization": f"Bearer {self._token}"}
        if self.config.project_id:
            headers["x-goog-user-project"] = self.config.project_id
        return headers

    @staticmethod
    def _base_url_for(loc: str) -> str:
        if loc == "global":
            return "https://aiplatform.googleapis.com"
        return f"https://{loc}-aiplatform.googleapis.com"

    @property
    def _base_url(self) -> str:
        return self._base_url_for(self.config.location)

    def build_openai_url(self, path: str, model: str = "") -> str:
        loc = self.config.resolve_location(model) if model else self.config.location
        pid = self.config.project_id
        base = self._base_url_for(loc)
        return (
            f"{base}/{self.config.api_version}"
            f"/projects/{pid}/locations/{loc}/endpoints/openapi{path}"
        )

    def build_gemini_url(self, model: str, method: str) -> str:
        loc = self.config.resolve_location(model)
        pid = self.config.project_id
        base = self._base_url_for(loc)
        return (
            f"{base}/{self.config.api_version}"
            f"/projects/{pid}/locations/{loc}"
            f"/publishers/google/models/{model}:{method}"
        )

    def build_models_url(self, publisher: str) -> str:
        return f"{self._base_url}/v1beta1/publishers/{publisher}/models"

    async def _background_refresh_loop(self) -> None:
        await self._refresh_token()
        while True:
            try:
                await asyncio.sleep(BACKGROUND_INTERVAL * 60)
                if not self._is_valid():
                    async with self._lock:
                        if not self._is_valid():
                            await self._refresh_token()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[Background] Token refresh loop error: {e}")

    def start(self) -> None:
        if self.config.auto_refresh:
            self._bg_task = asyncio.create_task(self._background_refresh_loop())
            logger.info(
                f"[Background] Async token refresh scheduled every {BACKGROUND_INTERVAL} minutes"
            )

    def stop(self) -> None:
        if self._bg_task and not self._bg_task.done():
            self._bg_task.cancel()


class ApiKeyAuth(AuthProvider):
    def __init__(self, config: AppConfig):
        self.config = config
        self.api_key = config.api_key or ""

    async def get_headers(self) -> dict[str, str]:
        return {}

    def _append_key(self, url: str) -> str:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}key={self.api_key}"

    def build_openai_url(self, path: str, model: str = "") -> str:
        raise NotImplementedError(
            "Express mode has no OpenAI-compatible endpoint; "
            "use body conversion via build_gemini_url instead"
        )

    def build_gemini_url(self, model: str, method: str) -> str:
        return self._append_key(
            f"https://aiplatform.googleapis.com/{self.config.api_version}"
            f"/publishers/google/models/{model}:{method}"
        )

    def build_models_url(self, publisher: str) -> str:
        return self._append_key(
            f"https://aiplatform.googleapis.com/v1beta1/publishers/{publisher}/models"
        )


class AIStudioAuth(AuthProvider):
    BASE = "https://generativelanguage.googleapis.com/v1beta"

    def __init__(self, config: AppConfig):
        self.api_key = config.gemini_api_key or ""

    async def get_headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key}

    def build_openai_url(self, path: str, model: str = "") -> str:
        raise NotImplementedError(
            "AI Studio has no OpenAI-compatible endpoint; "
            "use body conversion via build_gemini_url instead"
        )

    def build_gemini_url(self, model: str, method: str) -> str:
        return f"{self.BASE}/models/{model}:{method}"

    def build_models_url(self, publisher: str) -> str:
        return f"{self.BASE}/models"


def get_gcloud_project_id() -> str:
    from google.auth import default

    _, project_id = default()
    if not project_id:
        raise RuntimeError(
            "Project ID not found in ADC. Please specify VERTEX_PROJECT_ID "
            "or set up gcloud authentication"
        )
    return project_id


def create_auth(config: AppConfig) -> AuthProvider:
    if config.auth_mode == "aistudio":
        return AIStudioAuth(config)
    if config.auth_mode == "api_key":
        return ApiKeyAuth(config)
    return ServiceAccountAuth(config)
