"""Canonical ledger ingestion and derived read models."""

from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4


CAPABILITIES = {
    "ibkr": {"balances": True, "positions": True, "transactions": True, "tax_lots": True,
             "historical_import": True, "incremental_sync": True},
    "fidelity": {"balances": False, "positions": False, "transactions": False, "tax_lots": False,
                 "historical_import": True, "incremental_sync": False},
    "robinhood": {"balances": False, "positions": False, "transactions": False, "tax_lots": False,
                  "historical_import": True, "incremental_sync": False},
    "bofa": {"balances": False, "positions": False, "transactions": False, "tax_lots": False,
             "historical_import": True, "incremental_sync": False},
    "wealthfront": {"balances": False, "positions": False, "transactions": False, "tax_lots": False,
                    "historical_import": True, "incremental_sync": False},
}
SUPPORTED_TYPES = {"BUY", "SELL", "TRANSFER_IN"}


@dataclass(frozen=True)
class Transaction:
    id: str
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
    raw_content: str
    row_count: int


class LedgerStore:
    """Small in-memory store; replace with a repository backed by PostgreSQL in deployment."""

    def __init__(self) -> None:
        self.batches: dict[str, ImportBatch] = {}
        self.transactions: dict[str, Transaction] = {}
        self.event_ids: set[str] = set()

    def import_csv(self, household_id: str, connector: str, content: str) -> ImportBatch:
        if connector not in CAPABILITIES:
            raise ValueError(f"unsupported connector: {connector}")
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        existing = next((b for b in self.batches.values() if b.household_id == household_id
                         and b.content_hash == content_hash), None)
        if existing:
            return existing
        reader = csv.DictReader(io.StringIO(content))
        required = {"account_id", "symbol", "transaction_date", "quantity", "price", "type"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError(f"CSV requires columns: {', '.join(sorted(required))}")
        batch = ImportBatch(str(uuid4()), household_id, connector, content_hash,
                            datetime.now(timezone.utc), content, 0)
        imported = 0
        for row_number, row in enumerate(reader, start=2):
            transaction = self._normalize(row, batch.id, row_number)
            if transaction.source_id in self.event_ids:
                continue
            self.event_ids.add(transaction.source_id)
            self.transactions[transaction.id] = transaction
            imported += 1
        batch = ImportBatch(**{**asdict(batch), "row_count": imported})
        self.batches[batch.id] = batch
        return batch

    def for_household(self, household_id: str) -> list[Transaction]:
        batch_ids = {b.id for b in self.batches.values() if b.household_id == household_id}
        return [t for t in self.transactions.values() if t.batch_id in batch_ids]

    @staticmethod
    def _normalize(row: dict[str, str], batch_id: str, row_number: int) -> Transaction:
        try:
            kind = row["type"].strip().upper()
            if kind not in SUPPORTED_TYPES:
                raise ValueError(f"unsupported transaction type {kind!r}")
            quantity, price = Decimal(row["quantity"]), Decimal(row["price"])
            if quantity <= 0 or price < 0:
                raise ValueError("quantity must be positive and price cannot be negative")
            source_id = row.get("external_id", "").strip() or hashlib.sha256(
                "|".join(f"{key}={value}" for key, value in sorted(row.items())).encode()
            ).hexdigest()
            return Transaction(
                id=str(uuid4()), account_id=row["account_id"].strip(), symbol=row["symbol"].strip().upper(),
                transaction_date=date.fromisoformat(row["transaction_date"]), quantity=quantity,
                price=price, market_price=Decimal(row["market_price"]) if row.get("market_price") else None,
                transaction_type=kind,
                cost_basis=Decimal(row.get("cost_basis") or quantity * price),
                lot_id=row.get("lot_id", "").strip() or source_id, source_id=source_id,
                batch_id=batch_id,
            )
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ValueError(f"invalid row {row_number}: {exc}") from exc


store = LedgerStore()
