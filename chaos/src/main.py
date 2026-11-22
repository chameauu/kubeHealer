from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from chaos_mesh import ChaosMeshClient
from config import config, settings
from engine import ScenarioEngine
from events import configure_logging, log_event
from routers import scenarios
from simulator_client import SimulatorClient
from store import ScenarioStore


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    store = ScenarioStore(config.DB_PATH)
    engine = ScenarioEngine(
        store=store,
        chaos_mesh=ChaosMeshClient(),
        simulator=SimulatorClient(),
    )
    app.state.engine = engine
    await engine.start_sweeper()
    log_event("service.started", version=settings.version)
    try:
        yield
    finally:
        await engine.stop_sweeper()
        store.close()
        app.state.engine = None
        log_event("service.stopped")


app = FastAPI(title=settings.service_name, version=settings.version, lifespan=lifespan)
app.state.engine = None
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "version": settings.version}


app.include_router(scenarios.router)
