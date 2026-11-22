from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from events import configure_logging, log_event
from provisioning import InvalidStateError, QuotaExceededError, ResourceNotFoundError
from routers import faults, instances, state, subnets, volumes


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    log_event("service.started", version=settings.version)
    yield
    log_event("service.stopped")


app = FastAPI(title=settings.service_name, version=settings.version, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ResourceNotFoundError)
async def _resource_not_found(_: Request, exc: ResourceNotFoundError) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(InvalidStateError)
async def _invalid_state(_: Request, exc: InvalidStateError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(QuotaExceededError)
async def _quota_exceeded(_: Request, exc: QuotaExceededError) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.service_name, "version": settings.version}


app.include_router(volumes.router)
app.include_router(subnets.router)
app.include_router(instances.router)
app.include_router(faults.router)
app.include_router(state.router)
