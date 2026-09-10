"""Household access, people, and creation routes."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.domain.models import Profile
from app.services.application import household_app_service
from app.services.auth import require_profile
from app.services.ledger import repository


router = APIRouter(tags=["households"])


class CreateHouseholdRequest(BaseModel):
    name: str


class CreatePersonRequest(BaseModel):
    full_name: str


@router.get("/households")
def list_households(profile: Annotated[Profile, Depends(require_profile)]) -> dict:
    return {"households": [asdict(item) for item in repository.list_households(profile.id)]}


@router.post("/households", status_code=status.HTTP_201_CREATED)
def create_household(
    payload: CreateHouseholdRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        household = repository.create_household(profile.id, payload.name)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return {"household": asdict(household)}


@router.get("/households/{household_id}/people")
def list_people(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return {"people": household_app_service.list_people(profile.id, household_id)}
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/people", status_code=status.HTTP_201_CREATED)
def create_person(
    household_id: str,
    payload: CreatePersonRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return household_app_service.create_person(profile.id, household_id, full_name=payload.full_name)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
