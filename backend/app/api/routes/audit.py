"""Audit trail routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile


router = APIRouter(tags=["audit"])


@router.get("/households/{household_id}/audit")
def list_audit_events(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"events": household_app_service.list_audit(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
