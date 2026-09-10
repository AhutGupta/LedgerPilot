"""Read-only ledger import, dashboard, policy, connector, sync, and snapshot routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile
from app.services.ledger import CAPABILITIES


router = APIRouter(tags=["ledger"])


class CreatePolicyRequest(BaseModel):
    name: str
    target_allocations: dict[str, Any] = Field(default_factory=dict)
    rebalance_threshold_pct: Any = "5"
    cash_reserve_target_pct: Any = "0"
    max_single_position_pct: Any | None = None
    notes: str | None = None


class CreateConnectorRequest(BaseModel):
    connector: str
    display_name: str
    status: str = "pending"
    secret_provider: str = "env"
    secret_reference: str | None = None
    external_reference: str | None = None


class RecordSyncRequest(BaseModel):
    connector: str
    trigger: str = "manual"
    status: str | None = None
    summary: str | None = None


class CreateSnapshotRequest(BaseModel):
    snapshot_type: str = "dashboard"


class SaleSimulationRequest(BaseModel):
    symbol: str
    quantity: Any
    sale_price: Any
    account_id: str | None = None


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
        result = household_app_service.import_csv(profile.id, household_id, connector, body)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if result["idempotent"]:
        response.status_code = status.HTTP_200_OK
    return {
        "batch_id": result["batch"].id,
        "row_count": result["batch"].row_count,
        "content_hash": result["batch"].content_hash,
        "imported_at": result["batch"].imported_at,
        "idempotent": result["idempotent"],
        "snapshot_id": result["snapshot"].id,
        "sync_run_id": result["sync_run"].id,
    }


@router.get("/households/{household_id}/dashboard")
def get_dashboard(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.build_dashboard(profile.id, household_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/households/{household_id}/holdings")
def get_holdings(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        dashboard = household_app_service.build_dashboard(profile.id, household_id)
        return {"as_of": dashboard["as_of"], "holdings": dashboard["holdings"], "summary": dashboard["summary"]}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/households/{household_id}/tax-lots")
def get_tax_lots(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        dashboard = household_app_service.build_dashboard(profile.id, household_id)
        return {"as_of": dashboard["as_of"], "realized_gains": dashboard["realized_gains"], "tax_lots": dashboard["tax_lots"]}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/households/{household_id}/migration-plan")
def get_migration_plan(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        dashboard = household_app_service.build_dashboard(profile.id, household_id)
        return {
            "as_of": dashboard["as_of"],
            "migration_plan": dashboard["migration_plan"],
            "recommendations": dashboard["recommendations"],
        }
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/simulate-sale")
def post_sale_simulation(
    household_id: str,
    payload: SaleSimulationRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.simulate_sale(
            profile.id,
            household_id,
            symbol=payload.symbol,
            quantity=payload.quantity,
            sale_price=payload.sale_price,
            account_id=payload.account_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/households/{household_id}/policies")
def list_policies(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"policies": household_app_service.list_policies(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/policies", status_code=status.HTTP_201_CREATED)
def create_policy(
    household_id: str,
    payload: CreatePolicyRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.save_policy(
            profile.id,
            household_id,
            name=payload.name,
            target_allocations=payload.target_allocations,
            rebalance_threshold_pct=payload.rebalance_threshold_pct,
            cash_reserve_target_pct=payload.cash_reserve_target_pct,
            max_single_position_pct=payload.max_single_position_pct,
            notes=payload.notes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/households/{household_id}/connectors")
def list_connectors(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"connectors": household_app_service.list_connectors(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/connectors", status_code=status.HTTP_201_CREATED)
def create_connector(
    household_id: str,
    payload: CreateConnectorRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.register_connector(
            profile.id,
            household_id,
            connector=payload.connector,
            display_name=payload.display_name,
            status=payload.status,
            secret_provider=payload.secret_provider,
            secret_reference=payload.secret_reference,
            external_reference=payload.external_reference,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/households/{household_id}/syncs")
def list_syncs(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"sync_runs": household_app_service.list_syncs(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/syncs", status_code=status.HTTP_201_CREATED)
def create_sync(
    household_id: str,
    payload: RecordSyncRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.record_sync(
            profile.id,
            household_id,
            connector=payload.connector,
            trigger=payload.trigger,
            status=payload.status,
            summary=payload.summary,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc


@router.get("/households/{household_id}/snapshots")
def list_snapshots(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"snapshots": household_app_service.list_snapshots(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/snapshots", status_code=status.HTTP_201_CREATED)
def create_snapshot(
    household_id: str,
    payload: CreateSnapshotRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"snapshot": household_app_service._snapshot_payload(household_app_service.capture_snapshot(profile.id, household_id, snapshot_type=payload.snapshot_type))}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
