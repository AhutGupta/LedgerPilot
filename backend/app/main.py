"""Read-only LedgerPilot API."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import ledger

app = FastAPI(title="LedgerPilot", version="0.1.0")
app.include_router(ledger.router, prefix="/api/v1")
app.mount("/", StaticFiles(directory="app/web", html=True), name="web")
