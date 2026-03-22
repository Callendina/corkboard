"""FastAPI app setup, lifespan, router mounting."""
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from corkboard.config import load_config
from corkboard.database import init_db
from corkboard.routes.board import router as board_router, init_board_routes
from corkboard.routes.admin import router as admin_router, init_admin_routes
from corkboard.routes.dev_api import router as dev_api_router, init_dev_api_routes

config = load_config()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db(config.database_url)
    init_board_routes(config)
    init_admin_routes(config)
    init_dev_api_routes(config)

    yield


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
    try:
        count = subprocess.check_output(
            ["git", "rev-list", "--count", "HEAD"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return {"version": int(count)}
    except Exception:
        return {"version": 0}
