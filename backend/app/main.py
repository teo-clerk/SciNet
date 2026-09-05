"""SciNet API.

Binds to 127.0.0.1 only. This process is read-oriented: it serves the graph and
enqueues jobs, but the worker process is the sole writer of paper data.
"""

from __future__ import annotations

import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.model_store import configure_environment
from app.core.warmup import WARMER
from app.routers import (
    clusters,
    events,
    graph,
    jobs,
    librarian,
    models,
    papers,
    quantities,
    samples,
    search,
    system,
)

logger = logging.getLogger(__name__)


LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _warn_if_exposed() -> None:
    """Refuse to start quietly on a non-loopback address.

    This API has no authentication, serves arbitrary files from the library,
    and can launch a local process. On a laptop that is fine because only the
    machine itself can reach it; on 0.0.0.0 it is a file server and a remote
    exec surface for anyone on the network. uvicorn's --host is a command-line
    flag, so the default in settings does not actually prevent this — the check
    has to happen at startup.
    """
    import os
    import sys

    bound = os.environ.get("SCINET_BOUND_HOST", "")
    argv = " ".join(sys.argv)
    exposed = bound not in LOOPBACK and bound != ""
    if "--host" in argv:
        parts = sys.argv
        with contextlib.suppress(ValueError, IndexError):
            exposed = parts[parts.index("--host") + 1] not in LOOPBACK

    if exposed:
        logger.warning(
            "SciNet is bound to a non-loopback address. It has no "
            "authentication, serves files from the library and can launch "
            "processes. Anyone who can reach this port has those capabilities."
        )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.ensure_dirs()
    # Before the warmup thread imports sentence-transformers. The API loads the
    # embedding model too — for search queries — and without this it downloads
    # its own copy into the user's home cache instead of using the project's.
    applied = configure_environment(settings)
    logger.info("model cache: %s", applied["HF_HOME"])
    _warn_if_exposed()
    # Dispatched, not awaited. The embedding model takes ~25s to load and the
    # map does not need it at all, so startup returns immediately and the load
    # finishes on a background thread. Search reports "warming" until it lands.
    WARMER.start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="SciNet",
        description="A local, privacy-first map of everything you have read",
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
    for module in (
        system,
        papers,
        graph,
        clusters,
        search,
        jobs,
        events,
        models,
        librarian,
        quantities,
        samples,
    ):
        app.include_router(module.router)
    return app


app = create_app()
