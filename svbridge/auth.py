from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from threading import RLock

from apscheduler.schedulers.background import BackgroundScheduler

from .config import AppConfig, save_token

logger = logging.getLogger("svbridge")

TOKEN_EXPIRY_BUFFER = timedelta(minutes=10)
BACKGROUND_INTERVAL = 5  # minutes


class AuthProvider(ABC):
    @abstractmethod
    async def get_headers(self) -> dict[str, str]: ...

    @abstractmethod
    def build_openai_url(self, path: str) -> str: ...

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
        self._lock = RLock()
        self._scheduler: BackgroundScheduler | None = None

    def _generate_token(self) -> tuple[str, datetime] | tuple[None, None]:
        from google.auth import default
        from google.auth.transport.requests import Request

        try:
            credentials, _ = default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            credentials.refresh(Request())
            token = credentials.token
            expiry = credentials.expiry.replace(tzinfo=timezone.utc)
            return token, expiry
        except Exception as e:
            logger.error(f"[Token] Failed to fetch token: {e}")
            return None, None

    def _is_valid(self) -> bool:
        if not self.config.access_token or not self.config.token_expiry:
            return False
        expiry = datetime.fromisoformat(self.config.token_expiry)
        return datetime.now(timezone.utc) + TOKEN_EXPIRY_BUFFER < expiry

    def refresh_token(self, force: bool = False) -> bool:
        with self._lock:
            if not force and self._is_valid():
                logger.info("[Token] No refresh needed")
                return True
            new_token, new_exp = self._generate_token()
            if new_token and new_exp:
                self.config.access_token = new_token
                self.config.token_expiry = new_exp.isoformat()
                save_token(self.config)
                logger.info("[Token] Token refreshed")
                return True
            logger.error("[Token] Token refresh failed")
            return False

    async def get_headers(self) -> dict[str, str]:
        if not self._is_valid():
            await asyncio.to_thread(self.refresh_token, True)
        token = self.config.access_token
        if not token:
            raise RuntimeError("No valid token available")
        headers = {"Authorization": f"Bearer {token}"}
        if self.config.project_id:
            headers["x-goog-user-project"] = self.config.project_id
        return headers

    @property
    def _base_url(self) -> str:
        loc = self.config.location
        if loc == "global":
            return "https://aiplatform.googleapis.com"
        return f"https://{loc}-aiplatform.googleapis.com"

    def build_openai_url(self, path: str) -> str:
        loc = self.config.location
        pid = self.config.project_id
        return (
            f"{self._base_url}/v1"
            f"/projects/{pid}/locations/{loc}/endpoints/openapi{path}"
        )

    def build_gemini_url(self, model: str, method: str) -> str:
        loc = self.config.location
        pid = self.config.project_id
        return (
            f"{self._base_url}/v1"
            f"/projects/{pid}/locations/{loc}"
            f"/publishers/google/models/{model}:{method}"
        )

    def build_models_url(self, publisher: str) -> str:
        return (
            f"{self._base_url}"
            f"/v1beta1/publishers/{publisher}/models"
        )

    def start(self) -> None:
        self.refresh_token()
        if self.config.auto_refresh:
            self._scheduler = BackgroundScheduler()
            self._scheduler.add_job(
                self.refresh_token, "interval", minutes=BACKGROUND_INTERVAL
            )
            self._scheduler.start()
            logger.info(
                f"[Background] Token refresh every {BACKGROUND_INTERVAL} minutes"
            )

    def stop(self) -> None:
        if self._scheduler:
            self._scheduler.shutdown(wait=False)


class ApiKeyAuth(AuthProvider):
    def __init__(self, config: AppConfig):
        self.api_key = config.api_key or ""

    async def get_headers(self) -> dict[str, str]:
        return {}

    def _append_key(self, url: str) -> str:
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}key={self.api_key}"

    def build_openai_url(self, path: str) -> str:
        raise NotImplementedError(
            "Express mode has no OpenAI-compatible endpoint; "
            "use body conversion via build_gemini_url instead"
        )

    def build_gemini_url(self, model: str, method: str) -> str:
        return self._append_key(
            f"https://aiplatform.googleapis.com/v1"
            f"/publishers/google/models/{model}:{method}"
        )

    def build_models_url(self, publisher: str) -> str:
        return self._append_key(
            f"https://aiplatform.googleapis.com"
            f"/v1beta1/publishers/{publisher}/models"
        )


def get_gcloud_project_id() -> str:
    from google.auth import default

    _, project_id = default()
    assert project_id, "Project ID not found, please set up gcloud authentication"
    return project_id


def create_auth(config: AppConfig) -> AuthProvider:
    if config.auth_mode == "api_key":
        return ApiKeyAuth(config)
    return ServiceAccountAuth(config)
