"""Webhook ingestion routes for external providers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.services.application import household_app_service


router = APIRouter(tags=["webhooks"])


class PlaidWebhookPayload(BaseModel):
    webhook_type: str = Field(min_length=1)
    webhook_code: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    error: dict[str, Any] | None = None


@router.post("/webhooks/plaid", status_code=status.HTTP_200_OK)
def receive_plaid_webhook(payload: PlaidWebhookPayload) -> dict:
    return household_app_service.handle_plaid_webhook(
        webhook_type=payload.webhook_type,
        webhook_code=payload.webhook_code,
        item_id=payload.item_id,
        error=payload.error,
    )
