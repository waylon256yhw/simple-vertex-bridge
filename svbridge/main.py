from __future__ import annotations

import argparse
import logging
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import APIRouter, FastAPI, Request

from .auth import AuthProvider, create_auth, get_gcloud_project_id
from .config import AppConfig, load_config
from .routes import gemini_router, router
from .routes import init as init_routes

logger = logging.getLogger("svbridge")

VERSION = "0.4.1"

http_client: httpx.AsyncClient | None = None
auth: AuthProvider | None = None
app_config: AppConfig | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup()
    yield
    await shutdown()


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def normalize_path_middleware(request: Request, call_next):
    path = request.scope.get("path", "")
    normalized = path
    for dup in (
        "/v1beta/v1beta/",
        "/v1/v1beta/",
        "/v1beta/v1/",
        "/v1/v1/",
        "/v1beta1/v1beta1/",
    ):
        if normalized.startswith(dup):
            target = "/v1beta/" if "v1beta" in dup else "/v1/"
            normalized = target + normalized[len(dup) :]
            break
    if normalized != path:
        request.scope["path"] = normalized
    return await call_next(request)


app.include_router(router)
app.include_router(gemini_router, prefix="/v1")
app.include_router(gemini_router, prefix="/v1beta")
app.include_router(gemini_router, prefix="/v1beta1")
app.include_router(gemini_router)

root_router = APIRouter()


@root_router.get("/")
async def root():
    mode = app_config.auth_mode if app_config else "unknown"
    return {"status": "ok", "version": VERSION, "auth_mode": mode}


app.include_router(root_router)


async def startup():
    global http_client, auth, app_config

    setup_logging()
    app_config = load_config()

    http_client = httpx.AsyncClient(
        http2=True,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        timeout=httpx.Timeout(connect=10, read=600, write=60, pool=30),
    )

    if app_config.auth_mode == "service_account" and not app_config.project_id:
        logger.info("[Google] Getting project ID from ADC...")
        app_config.project_id = get_gcloud_project_id()

    auth = create_auth(app_config)
    auth.start()

    init_routes(app_config, auth, http_client)


async def shutdown():
    if auth:
        auth.stop()
    if http_client:
        await http_client.aclose()
    logger.info("[Shutdown] Cleanup complete")


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def print_banner(cfg: AppConfig):
    mode_display = {
        "aistudio": "AI Studio (generativelanguage.googleapis.com)",
        "api_key": "Vertex Express API Key (aiplatform.googleapis.com)",
        "service_account": f"Vertex Service Account (Project: {cfg.project_id or 'auto'}, Location: {cfg.location})",
    }.get(cfg.auth_mode, cfg.auth_mode)

    key_display = (
        f"Protected (Key: {cfg.proxy_key[:3]}***)" if cfg.proxy_key else "Open (No PROXY_KEY set)"
    )

    logger.info("=" * 62)
    logger.info(f"  Simple Vertex Bridge v{VERSION}")
    logger.info(f"  Auth Mode : {mode_display}")
    logger.info(f"  Server    : http://{cfg.bind}:{cfg.port}")
    logger.info(f"  Security  : {key_display}")
    logger.info("=" * 62)


def main():
    parser = argparse.ArgumentParser(description="Simple Vertex Bridge")
    parser.add_argument("-p", "--port", type=int, help="Port (default: 8086)")
    parser.add_argument("-b", "--bind", type=str, help="Bind address (default: localhost)")
    parser.add_argument("-k", "--key", type=str, help="Proxy authentication key")
    parser.add_argument(
        "--auto-refresh",
        action=argparse.BooleanOptionalAction,
        dest="auto_refresh",
        help="Background token refresh (default: on)",
    )
    parser.add_argument(
        "--filter-model-names",
        action=argparse.BooleanOptionalAction,
        dest="filter_model_names",
        help="Filter common model names (default: on)",
    )

    args = parser.parse_args()

    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    if args.bind is not None:
        os.environ["BIND"] = args.bind
    if args.key is not None:
        os.environ["PROXY_KEY"] = args.key
    if args.auto_refresh is not None:
        os.environ["AUTO_REFRESH"] = str(args.auto_refresh).lower()
    if args.filter_model_names is not None:
        os.environ["FILTER_MODEL_NAMES"] = str(args.filter_model_names).lower()

    cfg = load_config()

    setup_logging()
    print_banner(cfg)

    uvicorn.run(
        "svbridge.main:app",
        host=cfg.bind,
        port=cfg.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
