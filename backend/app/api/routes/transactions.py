"""Read-only transaction routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile


router = APIRouter(tags=["transactions"])


@router.get("/households/{household_id}/transactions")
def list_transactions(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
    person_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    try:
        return {
            "transactions": household_app_service.list_transactions(
                profile.id,
                household_id,
                person_id=person_id,
                limit=limit,
            )
        }
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
