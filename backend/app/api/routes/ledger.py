"""Read-only ledger import and portfolio analysis routes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status

from app.domain.models import Profile
from app.services.auth import require_profile
from app.services.engines import holdings, migration_plan, tax_lots
from app.services.ledger import CAPABILITIES, repository


router = APIRouter(tags=["ledger"])


@router.get("/connectors")
def connectors() -> dict:
    return {
        "connectors": [
            {"name": name, "capabilities": capabilities}
            for name, capabilities in CAPABILITIES.items()
        ]
    }


@router.post("/households/{household_id}/imports/{connector}", status_code=status.HTTP_201_CREATED)
def import_csv(
    household_id: str,
    connector: str,
    body: Annotated[str, Body(media_type="text/csv")],
    response: Response,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        batch, idempotent = repository.import_csv(profile.id, household_id, connector, body)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if idempotent:
        response.status_code = status.HTTP_200_OK
    return {
        "batch_id": batch.id,
        "row_count": batch.row_count,
        "content_hash": batch.content_hash,
        "imported_at": batch.imported_at,
        "idempotent": idempotent,
    }


@router.get("/households/{household_id}/dashboard")
def get_dashboard(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    return _snapshot(
        profile.id,
        household_id,
        lambda transactions: {
            "holdings": holdings(transactions),
            "tax_lots": tax_lots(transactions, date.today()),
            "migration_plan": migration_plan(transactions, date.today()),
            "imports": [
                {
                    "id": batch.id,
                    "connector": batch.connector,
                    "content_hash": batch.content_hash,
                    "imported_at": batch.imported_at,
                    "row_count": batch.row_count,
                }
                for batch in repository.list_import_batches(profile.id, household_id)
            ],
        },
    )


@router.get("/households/{household_id}/holdings")
def get_holdings(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    return _snapshot(profile.id, household_id, lambda transactions: {"holdings": holdings(transactions)})


@router.get("/households/{household_id}/tax-lots")
def get_tax_lots(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    return _snapshot(
        profile.id,
        household_id,
        lambda transactions: {"tax_lots": tax_lots(transactions, date.today())},
    )


@router.get("/households/{household_id}/migration-plan")
def get_migration_plan(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    return _snapshot(
        profile.id,
        household_id,
        lambda transactions: {"migration_plan": migration_plan(transactions, date.today())},
    )


def _snapshot(profile_id: str, household_id: str, build_payload) -> dict:
    try:
        transactions = repository.for_household(profile_id, household_id)
        metadata = repository.snapshot_metadata(profile_id, household_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return {
        **build_payload(transactions),
        **metadata,
        "as_of": datetime.now(timezone.utc),
    }
