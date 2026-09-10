"""Recommendation routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile


router = APIRouter(tags=["recommendations"])


@router.get("/households/{household_id}/recommendations")
def get_recommendations(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.build_recommendations(profile.id, household_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
