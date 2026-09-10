"""Household memory routes."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile


router = APIRouter(tags=["memory"])


class CreateMemoryRequest(BaseModel):
    entry_type: str
    content: str
    labels: list[str] = Field(default_factory=list)
    importance: str = "medium"


@router.get("/households/{household_id}/memory")
def list_memory_entries(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"entries": household_app_service.list_memory(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/memory", status_code=status.HTTP_201_CREATED)
def create_memory_entry(
    household_id: str,
    payload: CreateMemoryRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.add_memory_entry(
            profile.id,
            household_id,
            entry_type=payload.entry_type,
            content=payload.content,
            labels=payload.labels,
            importance=payload.importance,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
