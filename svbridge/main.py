from __future__ import annotations

import argparse
import logging
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI

from .auth import AuthProvider, create_auth, get_gcloud_project_id
from .config import AppConfig, load_config
from .routes import init as init_routes, router, v1beta_router

logger = logging.getLogger("svbridge")

http_client: httpx.AsyncClient | None = None
auth: AuthProvider | None = None
app_config: AppConfig | None = None


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await startup()
    yield
    await shutdown()


app = FastAPI(lifespan=lifespan)
app.include_router(router)
app.include_router(v1beta_router)

# Also mount without /v1 prefix for backward compat
from fastapi import APIRouter

root_router = APIRouter()


@root_router.get("/")
async def root():
    mode = app_config.auth_mode if app_config else "unknown"
    return {"status": "ok", "auth_mode": mode}


app.include_router(root_router)


async def startup():
    global http_client, auth, app_config

    app_config = load_config()
    logger.info(f"[Config] Auth mode: {app_config.auth_mode}")

    http_client = httpx.AsyncClient(
        http2=True,
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        timeout=httpx.Timeout(connect=10, read=600, write=60, pool=30),
    )

    if app_config.auth_mode == "service_account":
        logger.info("[Google] Getting project ID...")
        app_config.project_id = get_gcloud_project_id()
        logger.info(f"[Google] Project: {app_config.project_id}")
        logger.info(f"[Google] Location: {app_config.location}")

    auth = create_auth(app_config)
    auth.start()

    init_routes(app_config, auth, http_client)

    if app_config.auth_mode == "api_key":
        logger.info("[Mode] API Key (Express) | Global endpoint")
    else:
        logger.info(
            f"[Mode] Service Account | "
            f"Project: {app_config.project_id} | "
            f"Location: {app_config.location}"
        )


async def shutdown():
    if auth:
        auth.stop()
    if http_client:
        await http_client.aclose()
    logger.info("[Shutdown] Cleanup complete")


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

    # CLI args override env vars (env vars are read in load_config)
    import os
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

    bind = cfg.bind
    port = cfg.port
    key = cfg.proxy_key

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    logger.info("--------")
    logger.info(f"Server: http://{bind}:{port}")
    logger.info(f"Auth mode: {cfg.auth_mode}")
    if bind not in ("localhost", "127.0.0.1", "::1") and not key:
        logger.warning("[Auth] Server is exposed without a key, PLEASE SET PROXY_KEY!")
    elif key:
        logger.info(f'Proxy key: "{key}"')
    logger.info("--------")

    uvicorn.run("svbridge.main:app", host=bind, port=port)


if __name__ == "__main__":
    main()
