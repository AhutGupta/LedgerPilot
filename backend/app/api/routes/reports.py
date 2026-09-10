"""Deterministic report routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile


router = APIRouter(tags=["reports"])


@router.get("/households/{household_id}/reports/quarterly-review")
def quarterly_review(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.build_report(profile.id, household_id, report_type="quarterly_review")
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/households/{household_id}/reports/ytd-realized-gains")
def ytd_realized_gains(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.build_report(profile.id, household_id, report_type="ytd_realized_gains")
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
