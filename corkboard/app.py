"""FastAPI app setup, lifespan, router mounting."""
import os
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from corkboard.config import load_config


# App version: git commit count // 100 (Callendina fleet convention).
def _corkboard_version() -> int:
    try:
        count = int(subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=str(Path(__file__).parent.parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip())
        return count // 100
    except Exception:
        return 0


APP_VERSION = _corkboard_version()

config = load_config()


# Cyclops env vars must be set before `import cyclops`. Corkboard has no
# environment in its own config (it's a single-instance multi-tenant
# service), so ENVIRONMENT comes from the host env var, defaulting to prod.
os.environ.setdefault("APP_NAME", "corkboard")
os.environ.setdefault(
    "ENVIRONMENT",
    "staging" if (os.environ.get("CORKBOARD_ENV") or "").lower().startswith("staging") else "prod",
)
os.environ.setdefault("APP_VERSION", f"v{APP_VERSION}")
os.environ.setdefault("CYCLOPS_COMPONENT", "corkboard.web")

import cyclops  # noqa: E402

from corkboard.database import init_db  # noqa: E402
from corkboard.routes.board import router as board_router, init_board_routes  # noqa: E402
from corkboard.routes.admin import router as admin_router, init_admin_routes  # noqa: E402
from corkboard.routes.dev_api import router as dev_api_router, init_dev_api_routes  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(config.database_url)
    init_board_routes(config)
    init_admin_routes(config)
    init_dev_api_routes(config)

    cyclops.app_started(app_count=len(config.apps))
    try:
        yield
    finally:
        cyclops.app_stopped()


app = FastAPI(title="Corkboard", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1", "localhost"])

prefix = config.mount_prefix
app.include_router(board_router, prefix=prefix)
app.include_router(admin_router, prefix=prefix)
app.include_router(dev_api_router, prefix=prefix)

# Static files
static_dir = Path(__file__).parent / "static"
if static_dir.is_dir():
    app.mount(f"{prefix}/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/version")
async def version():
    return {"version": APP_VERSION}
