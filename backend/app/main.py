"""Read-only LedgerPilot API."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import accounts, ai, audit, auth, households, ledger, memory, recommendations, reports, transactions
from app.config import get_settings
from app.db.session import run_migrations
from app.services.raw_uploads import raw_upload_store


@asynccontextmanager
async def lifespan(_: FastAPI):
    raw_upload_store.ensure_root()
    run_migrations()
    yield


settings = get_settings()
app = FastAPI(title="LedgerPilot", version="0.3.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth.router, prefix="/api/v1")
app.include_router(households.router, prefix="/api/v1")
app.include_router(ledger.router, prefix="/api/v1")
app.include_router(accounts.router, prefix="/api/v1")
app.include_router(transactions.router, prefix="/api/v1")
app.include_router(memory.router, prefix="/api/v1")
app.include_router(audit.router, prefix="/api/v1")
app.include_router(recommendations.router, prefix="/api/v1")
app.include_router(reports.router, prefix="/api/v1")
app.include_router(ai.router, prefix="/api/v1")


@app.get("/")
def root() -> dict:
    return {
        "service": "LedgerPilot",
        "api": "/api/v1",
        "frontend": settings.frontend_origin,
        "docs": "/docs",
    }


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
