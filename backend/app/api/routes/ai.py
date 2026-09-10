"""Tenant-authorized AI tool contract and execution routes."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.ai.tools import ai_tool_registry
from app.domain.models import Profile
from app.services.auth import require_profile


router = APIRouter(tags=["ai"])


class ExecuteToolRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


@router.get("/households/{household_id}/ai/tools")
def tool_contract(
    household_id: str,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return ai_tool_registry.contract(profile.id, household_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/households/{household_id}/ai/tools/{tool_name}")
def execute_tool(
    household_id: str,
    tool_name: str,
    payload: ExecuteToolRequest,
    profile: Annotated[Profile, Depends(require_profile)],
) -> dict:
    try:
        return ai_tool_registry.execute(profile.id, household_id, tool_name, payload.arguments)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
