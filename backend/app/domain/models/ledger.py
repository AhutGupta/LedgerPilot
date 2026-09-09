from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass(frozen=True)
class Profile:
    id: str
    email: str
    full_name: str
    created_at: datetime


@dataclass(frozen=True)
class StoredProfileCredentials:
    profile: Profile
    password_hash: str


@dataclass(frozen=True)
class HouseholdSummary:
    id: str
    name: str
    role: str
    created_at: datetime


@dataclass(frozen=True)
class Transaction:
    id: str
    household_id: str
    account_id: str
    symbol: str
    transaction_date: date
    quantity: Decimal
    price: Decimal
    market_price: Decimal | None
    transaction_type: str
    cost_basis: Decimal
    lot_id: str
    source_id: str
    batch_id: str


@dataclass(frozen=True)
class ImportBatch:
    id: str
    household_id: str
    connector: str
    content_hash: str
    imported_at: datetime
    raw_upload_path: str
    row_count: int
