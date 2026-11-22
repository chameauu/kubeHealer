from __future__ import annotations

import asyncio
import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.sqlite import SqliteSaver

from config import config, settings
from events import configure_logging, log_event
from graph import build_graph, is_pending
from providers import build_provider
from routers import runs as runs_router
from store import store
from sweeper import sweep_forever


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log_event("service.started", version=settings.version)

    # Fail closed: an unknown LLM_MODE stops the service here, not mid-run.
    provider = build_provider(config)
    log_event(
        "agent.diagnosis_provider",
        mode=config.LLM_MODE,
        model=provider.model,
    )

    checkpoint_path = Path(config.CHECKPOINT_PATH).expanduser()
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    graph = build_graph(checkpointer=SqliteSaver(connection))
    runs_router.graph = graph

    changed = await store.reconcile_interrupted(
        lambda scenario_id: is_pending(graph, scenario_id)
    )
    if changed:
        log_event("agent.reconciled", scenario_ids=changed)

    sweeper = asyncio.create_task(sweep_forever(graph))
    try:
        yield
    finally:
        sweeper.cancel()
        with suppress(asyncio.CancelledError):
            await sweeper
        runs_router.graph = None
        connection.close()
        log_event("service.stopped")


app = FastAPI(title=settings.service_name, version=settings.version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(runs_router.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "version": settings.version}
