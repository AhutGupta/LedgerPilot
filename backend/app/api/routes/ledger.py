"""Read-only ledger import and portfolio analysis routes."""

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Response

from app.services.engines import holdings, migration_plan, tax_lots
from app.services.ledger import CAPABILITIES, store

router = APIRouter(tags=["ledger"])


@router.get("/connectors")
def connectors() -> dict:
    return {"connectors": [{"name": name, "capabilities": capabilities}
                           for name, capabilities in CAPABILITIES.items()]}


@router.post("/households/{household_id}/imports/{connector}", status_code=201)
def import_csv(
    household_id: str,
    connector: str,
    body: Annotated[str, Body(media_type="text/csv")],
    response: Response,
) -> dict:
    try:
        before = len(store.batches)
        batch = store.import_csv(household_id, connector, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if len(store.batches) == before:
        response.status_code = 200
    return {"batch_id": batch.id, "row_count": batch.row_count, "content_hash": batch.content_hash,
            "imported_at": batch.imported_at, "idempotent": response.status_code == 200}


@router.get("/households/{household_id}/holdings")
def get_holdings(household_id: str) -> dict:
    return _snapshot(household_id, {"holdings": holdings(store.for_household(household_id))})


@router.get("/households/{household_id}/tax-lots")
def get_tax_lots(household_id: str) -> dict:
    return _snapshot(household_id, {"tax_lots": tax_lots(store.for_household(household_id), date.today())})


@router.get("/households/{household_id}/migration-plan")
def get_migration_plan(household_id: str) -> dict:
    return _snapshot(household_id, {"migration_plan": migration_plan(store.for_household(household_id), date.today())})


def _snapshot(household_id: str, payload: dict) -> dict:
    batches = [b for b in store.batches.values() if b.household_id == household_id]
    return {**payload, "as_of": datetime.now(timezone.utc), "source": "canonical_ledger",
            "freshness": "current" if batches else "unavailable", "sync_status": "success" if batches else "never_synced",
            "calculation_version": "1"}
