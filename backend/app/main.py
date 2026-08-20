"""SciNet API.

Binds to 127.0.0.1 only. This process is read-oriented: it serves the graph and
enqueues jobs, but the worker process is the sole writer of paper data.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import events, graph, jobs, papers, system


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings().ensure_dirs()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SciNet",
        description="Local privacy-first scientific paper map",
        version=system.VERSION,
        lifespan=lifespan,
    )
    # Locked to the Vite dev origin: a page in another tab can still reach
    # localhost, so this is not decoration.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["*"],
    )
    for module in (system, papers, graph, jobs, events):
        app.include_router(module.router)
    return app


app = create_app()
