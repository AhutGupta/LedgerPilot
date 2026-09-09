"""Read-only LedgerPilot API."""

from fastapi import FastAPI

from app.api.routes import ledger

app = FastAPI(title="LedgerPilot", version="0.1.0")
app.include_router(ledger.router, prefix="/api/v1")
