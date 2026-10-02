"""Minimal Plaid Link and one-time investment-position import support."""

from __future__ import annotations

import csv
import io
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.config import get_settings


PLAID_BASE_URLS = {
    "sandbox": "https://sandbox.plaid.com",
    "development": "https://development.plaid.com",
    "production": "https://production.plaid.com",
}


class PlaidClient:
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.plaid_client_id or not settings.plaid_secret:
            raise ValueError("Plaid is not configured. Set LEDGERPILOT_PLAID_CLIENT_ID and LEDGERPILOT_PLAID_SECRET.")
        try:
            self.base_url = PLAID_BASE_URLS[settings.plaid_environment]
        except KeyError as exc:
            raise ValueError("LEDGERPILOT_PLAID_ENVIRONMENT must be sandbox, development, or production.") from exc
        self.client_id = settings.plaid_client_id
        self.secret = settings.plaid_secret

    def create_link_token(self, *, profile_id: str, person_name: str) -> str:
        result = self._post(
            "/link/token/create",
            {
                "client_name": "LedgerPilot",
                "country_codes": ["US"],
                "language": "en",
                "products": ["investments"],
                "user": {"client_user_id": profile_id},
            },
        )
        token = result.get("link_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Plaid did not return a Link token.")
        return token

    def exchange_and_get_holdings(self, public_token: str) -> tuple[str, str, dict]:
        if not public_token.strip():
            raise ValueError("Plaid public token is required.")
        exchanged = self._post("/item/public_token/exchange", {"public_token": public_token})
        access_token = exchanged.get("access_token")
        item_id = exchanged.get("item_id")
        if not isinstance(access_token, str) or not isinstance(item_id, str):
            raise ValueError("Plaid did not return an item access token.")
        holdings = self._post("/investments/holdings/get", {"access_token": access_token})
        return access_token, item_id, holdings

    def _post(self, path: str, payload: dict) -> dict:
        request_body = json.dumps(
            {"client_id": self.client_id, "secret": self.secret, **payload},
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=request_body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = _plaid_error_detail(exc)
            raise ValueError(f"Plaid request failed: {detail}") from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError("Plaid could not be reached. Please try again.") from exc
        if not isinstance(result, dict):
            raise ValueError("Plaid returned an invalid response.")
        return result


def holdings_to_csv(holdings_payload: dict, *, item_id: str, as_of: date | None = None) -> str:
    """Represent current Plaid positions as transfer-in lots for portfolio viewing only."""
    accounts = {
        item.get("account_id"): item
        for item in holdings_payload.get("accounts", [])
        if isinstance(item, dict) and isinstance(item.get("account_id"), str)
    }
    securities = {
        item.get("security_id"): item
        for item in holdings_payload.get("securities", [])
        if isinstance(item, dict) and isinstance(item.get("security_id"), str)
    }
    rows = []
    for holding in holdings_payload.get("holdings", []):
        if not isinstance(holding, dict):
            continue
        quantity = _decimal(holding.get("quantity"))
        if quantity is None or quantity <= 0:
            continue
        security = securities.get(holding.get("security_id"), {})
        account = accounts.get(holding.get("account_id"), {})
        price = _decimal(holding.get("institution_price")) or _decimal(security.get("close_price"))
        value = _decimal(holding.get("institution_value"))
        if price is None and value is not None:
            price = value / quantity
        if price is None or price < 0:
            continue
        security_id = str(holding.get("security_id") or "unknown")
        account_id = str(holding.get("account_id") or "unknown")
        symbol = str(security.get("ticker") or security.get("name") or f"PLAID-{security_id}").strip().upper()
        rows.append(
            {
                "account_id": account_id,
                "symbol": symbol,
                "transaction_date": (as_of or date.today()).isoformat(),
                "quantity": str(quantity),
                "price": str(price),
                "market_price": str(price),
                "type": "TRANSFER_IN",
                "cost_basis": str(quantity * price),
                "lot_id": f"plaid-{item_id}-{account_id}-{security_id}",
                "external_id": f"plaid-{item_id}-{account_id}-{security_id}",
            }
        )
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else [
        "account_id", "symbol", "transaction_date", "quantity", "price", "market_price",
        "type", "cost_basis", "lot_id", "external_id",
    ])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _decimal(value: object) -> Decimal | None:
    if value in {None, ""}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _plaid_error_detail(error: HTTPError) -> str:
    try:
        payload = json.loads(error.read().decode("utf-8"))
        return str(payload.get("error_message") or payload.get("display_message") or "request was rejected")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "request was rejected"
