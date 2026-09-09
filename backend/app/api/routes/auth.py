"""Authentication routes for profiles and household bootstrap."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.domain.models import Profile
from app.services.auth import create_access_token, hash_password, normalize_email, require_profile, verify_password
from app.services.ledger import repository


router = APIRouter(tags=["auth"])


class RegisterRequest(BaseModel):
    email: str
    full_name: str
    password: str
    household_name: str


class LoginRequest(BaseModel):
    email: str
    password: str


@router.post("/auth/register", status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest) -> dict:
    try:
        profile = repository.register_profile(
            email=normalize_email(payload.email),
            full_name=payload.full_name,
            password_hash=hash_password(payload.password),
            household_name=payload.household_name,
        )
    except ValueError as exc:
        status_code = status.HTTP_409_CONFLICT if "already exists" in str(exc) else status.HTTP_422_UNPROCESSABLE_ENTITY
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    return _auth_payload(profile)


@router.post("/auth/login")
def login(payload: LoginRequest) -> dict:
    try:
        email = normalize_email(payload.email)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    credentials = repository.find_profile_credentials(email)
    if credentials is None or not verify_password(payload.password, credentials.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid email or password")
    return _auth_payload(credentials.profile)


@router.get("/me")
def me(profile: Annotated[Profile, Depends(require_profile)]) -> dict:
    return {
        "profile": asdict(profile),
        "households": [asdict(item) for item in repository.list_households(profile.id)],
    }


def _auth_payload(profile) -> dict:
    return {
        "access_token": create_access_token(profile),
        "token_type": "bearer",
        "profile": asdict(profile),
        "households": [asdict(item) for item in repository.list_households(profile.id)],
    }
