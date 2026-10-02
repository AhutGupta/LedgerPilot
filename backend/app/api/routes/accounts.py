"""Read-only account routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile


router = APIRouter(tags=["accounts"])


@router.get("/households/{household_id}/accounts")
def list_accounts(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
    person_id: str | None = Query(default=None),
) -> dict:
    try:
        return {"accounts": household_app_service.list_accounts(profile.id, household_id, person_id=person_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
