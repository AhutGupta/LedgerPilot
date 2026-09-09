"""Canonical ledger persistence, normalization, and legacy in-memory harness."""

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from app.db.session import get_connection
from app.domain.models import HouseholdSummary, ImportBatch, Profile, StoredProfileCredentials, Transaction
from app.services.raw_uploads import raw_upload_store


CAPABILITIES = {
    "ibkr": {
        "balances": True,
        "positions": True,
        "transactions": True,
        "tax_lots": True,
        "historical_import": True,
        "incremental_sync": True,
    },
    "fidelity": {
        "balances": False,
        "positions": False,
        "transactions": False,
        "tax_lots": False,
        "historical_import": True,
        "incremental_sync": False,
    },
    "robinhood": {
        "balances": False,
        "positions": False,
        "transactions": False,
        "tax_lots": False,
        "historical_import": True,
        "incremental_sync": False,
    },
    "bofa": {
        "balances": False,
        "positions": False,
        "transactions": False,
        "tax_lots": False,
        "historical_import": True,
        "incremental_sync": False,
    },
    "wealthfront": {
        "balances": False,
        "positions": False,
        "transactions": False,
        "tax_lots": False,
        "historical_import": True,
        "incremental_sync": False,
    },
}
SUPPORTED_TYPES = {"BUY", "SELL", "TRANSFER_IN"}
REQUIRED_COLUMNS = {"account_id", "symbol", "transaction_date", "quantity", "price", "type"}


class LedgerStore:
    """Legacy in-memory harness retained for narrow unit tests."""

    def __init__(self) -> None:
        self.batches: dict[str, ImportBatch] = {}
        self.transactions: dict[str, Transaction] = {}
        self.event_ids: set[tuple[str, str]] = set()

    def import_csv(self, household_id: str, connector: str, content: str) -> ImportBatch:
        if connector not in CAPABILITIES:
            raise ValueError(f"unsupported connector: {connector}")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = next(
            (
                batch
                for batch in self.batches.values()
                if batch.household_id == household_id
                and batch.connector == connector
                and batch.content_hash == content_hash
            ),
            None,
        )
        if existing:
            return existing
        batch_id = str(uuid4())
        imported = 0
        for transaction in normalize_csv_rows(content, household_id, batch_id):
            event_key = (household_id, transaction.source_id)
            if event_key in self.event_ids:
                continue
            self.event_ids.add(event_key)
            self.transactions[transaction.id] = transaction
            imported += 1
        batch = ImportBatch(
            id=batch_id,
            household_id=household_id,
            connector=connector,
            content_hash=content_hash,
            imported_at=datetime.now(timezone.utc),
            raw_upload_path="memory://raw-upload",
            row_count=imported,
        )
        self.batches[batch.id] = batch
        return batch

    def for_household(self, household_id: str) -> list[Transaction]:
        batch_ids = {batch.id for batch in self.batches.values() if batch.household_id == household_id}
        return [transaction for transaction in self.transactions.values() if transaction.batch_id in batch_ids]


class LedgerRepository:
    def register_profile(self, email: str, full_name: str, password_hash: str, household_name: str) -> Profile:
        if not full_name.strip():
            raise ValueError("full_name is required")
        if not household_name.strip():
            raise ValueError("household_name is required")
        profile_id = str(uuid4())
        household_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            profile_row = connection.execute(
                """
                INSERT INTO profiles (id, email, full_name, password_hash, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO NOTHING
                RETURNING id, email, full_name, created_at
                """,
                (profile_id, email, full_name.strip(), password_hash, now),
            ).fetchone()
            if profile_row is None:
                raise ValueError("a profile with this email already exists")
            connection.execute(
                """
                INSERT INTO households (id, name, created_at, created_by_profile_id)
                VALUES (%s, %s, %s, %s)
                """,
                (household_id, household_name.strip(), now, profile_id),
            )
            connection.execute(
                """
                INSERT INTO household_memberships (profile_id, household_id, role, joined_at)
                VALUES (%s, %s, 'owner', %s)
                """,
                (profile_id, household_id, now),
            )
            connection.commit()
        return self._map_profile(profile_row)

    def find_profile_credentials(self, email: str) -> StoredProfileCredentials | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT id, email, full_name, created_at, password_hash
                FROM profiles
                WHERE email = %s
                """,
                (email,),
            ).fetchone()
        if row is None:
            return None
        return StoredProfileCredentials(profile=self._map_profile(row), password_hash=row["password_hash"])

    def get_profile(self, profile_id: str) -> Profile | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT id, email, full_name, created_at FROM profiles WHERE id = %s",
                (profile_id,),
            ).fetchone()
        return None if row is None else self._map_profile(row)

    def list_households(self, profile_id: str) -> list[HouseholdSummary]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT h.id, h.name, hm.role, h.created_at
                FROM households h
                JOIN household_memberships hm ON hm.household_id = h.id
                WHERE hm.profile_id = %s
                ORDER BY h.created_at, h.id
                """,
                (profile_id,),
            ).fetchall()
        return [self._map_household(row) for row in rows]

    def create_household(self, profile_id: str, name: str) -> HouseholdSummary:
        if not name.strip():
            raise ValueError("name is required")
        household_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_owner_profile(connection, profile_id)
            connection.execute(
                """
                INSERT INTO households (id, name, created_at, created_by_profile_id)
                VALUES (%s, %s, %s, %s)
                """,
                (household_id, name.strip(), now, profile_id),
            )
            connection.execute(
                """
                INSERT INTO household_memberships (profile_id, household_id, role, joined_at)
                VALUES (%s, %s, 'owner', %s)
                """,
                (profile_id, household_id, now),
            )
            connection.commit()
        return HouseholdSummary(id=household_id, name=name.strip(), role="owner", created_at=now)

    def import_csv(
        self,
        profile_id: str,
        household_id: str,
        connector: str,
        content: str,
    ) -> tuple[ImportBatch, bool]:
        if connector not in CAPABILITIES:
            raise ValueError(f"unsupported connector: {connector}")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        batch_id = str(uuid4())
        normalized = normalize_csv_rows(content, household_id, batch_id)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            existing = connection.execute(
                """
                SELECT id, household_id, connector, content_hash, imported_at, raw_upload_path, row_count
                FROM import_batches
                WHERE household_id = %s AND connector = %s AND content_hash = %s
                """,
                (household_id, connector, content_hash),
            ).fetchone()
            if existing is not None:
                return self._map_import_batch(existing), True
            raw_upload_path = raw_upload_store.store(household_id, connector, content_hash, content)
            created = connection.execute(
                """
                INSERT INTO import_batches (
                    id, household_id, connector, content_hash, raw_upload_path, row_count, imported_at, created_by_profile_id
                )
                VALUES (%s, %s, %s, %s, %s, 0, %s, %s)
                ON CONFLICT (household_id, connector, content_hash) DO NOTHING
                RETURNING id, household_id, connector, content_hash, imported_at, raw_upload_path, row_count
                """,
                (
                    batch_id,
                    household_id,
                    connector,
                    content_hash,
                    raw_upload_path,
                    datetime.now(timezone.utc),
                    profile_id,
                ),
            ).fetchone()
            if created is None:
                existing = connection.execute(
                    """
                    SELECT id, household_id, connector, content_hash, imported_at, raw_upload_path, row_count
                    FROM import_batches
                    WHERE household_id = %s AND connector = %s AND content_hash = %s
                    """,
                    (household_id, connector, content_hash),
                ).fetchone()
                return self._map_import_batch(existing), True
            inserted = 0
            for transaction in normalized:
                account_row = connection.execute(
                    """
                    INSERT INTO accounts (id, household_id, external_account_id, connector, display_name)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (household_id, external_account_id)
                    DO UPDATE SET connector = EXCLUDED.connector, display_name = EXCLUDED.display_name
                    RETURNING id
                    """,
                    (
                        str(uuid4()),
                        household_id,
                        transaction.account_id,
                        connector,
                        transaction.account_id,
                    ),
                ).fetchone()
                inserted_row = connection.execute(
                    """
                    INSERT INTO transactions (
                        id, household_id, account_id, account_external_id, symbol, transaction_date,
                        quantity, price, market_price, transaction_type, cost_basis, lot_id, source_id, batch_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (household_id, source_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        transaction.id,
                        transaction.household_id,
                        account_row["id"],
                        transaction.account_id,
                        transaction.symbol,
                        transaction.transaction_date,
                        transaction.quantity,
                        transaction.price,
                        transaction.market_price,
                        transaction.transaction_type,
                        transaction.cost_basis,
                        transaction.lot_id,
                        transaction.source_id,
                        transaction.batch_id,
                    ),
                ).fetchone()
                if inserted_row is not None:
                    inserted += 1
            batch_row = connection.execute(
                """
                UPDATE import_batches
                SET row_count = %s
                WHERE id = %s
                RETURNING id, household_id, connector, content_hash, imported_at, raw_upload_path, row_count
                """,
                (inserted, batch_id),
            ).fetchone()
            connection.commit()
        return self._map_import_batch(batch_row), False

    def for_household(self, profile_id: str, household_id: str) -> list[Transaction]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, account_external_id AS account_id, symbol, transaction_date,
                       quantity, price, market_price, transaction_type, cost_basis, lot_id, source_id, batch_id
                FROM transactions
                WHERE household_id = %s
                ORDER BY transaction_date, id
                """,
                (household_id,),
            ).fetchall()
        return [self._map_transaction(row) for row in rows]

    def list_import_batches(self, profile_id: str, household_id: str) -> list[ImportBatch]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, connector, content_hash, imported_at, raw_upload_path, row_count
                FROM import_batches
                WHERE household_id = %s
                ORDER BY imported_at DESC, id DESC
                """,
                (household_id,),
            ).fetchall()
        return [self._map_import_batch(row) for row in rows]

    def snapshot_metadata(self, profile_id: str, household_id: str) -> dict:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = connection.execute(
                """
                SELECT COUNT(*) AS batch_count, MAX(imported_at) AS latest_imported_at
                FROM import_batches
                WHERE household_id = %s
                """,
                (household_id,),
            ).fetchone()
        batch_count = int(row["batch_count"])
        return {
            "source": "canonical_ledger_postgresql",
            "freshness": "current" if batch_count else "unavailable",
            "sync_status": "success" if batch_count else "never_synced",
            "calculation_version": "2",
            "latest_imported_at": row["latest_imported_at"],
        }

    @staticmethod
    def _map_profile(row: dict) -> Profile:
        return Profile(id=row["id"], email=row["email"], full_name=row["full_name"], created_at=row["created_at"])

    @staticmethod
    def _map_household(row: dict) -> HouseholdSummary:
        return HouseholdSummary(id=row["id"], name=row["name"], role=row["role"], created_at=row["created_at"])

    @staticmethod
    def _map_import_batch(row: dict) -> ImportBatch:
        return ImportBatch(
            id=row["id"],
            household_id=row["household_id"],
            connector=row["connector"],
            content_hash=row["content_hash"],
            imported_at=row["imported_at"],
            raw_upload_path=row["raw_upload_path"],
            row_count=row["row_count"],
        )

    @staticmethod
    def _map_transaction(row: dict) -> Transaction:
        return Transaction(
            id=row["id"],
            household_id=row["household_id"],
            account_id=row["account_id"],
            symbol=row["symbol"],
            transaction_date=row["transaction_date"],
            quantity=row["quantity"],
            price=row["price"],
            market_price=row["market_price"],
            transaction_type=row["transaction_type"],
            cost_basis=row["cost_basis"],
            lot_id=row["lot_id"],
            source_id=row["source_id"],
            batch_id=row["batch_id"],
        )

    @staticmethod
    def _require_household_owner_profile(connection, profile_id: str) -> None:
        row = connection.execute(
            "SELECT id FROM profiles WHERE id = %s",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise LookupError("profile not found")

    @staticmethod
    def _require_household_access(connection, profile_id: str, household_id: str) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM household_memberships
            WHERE profile_id = %s AND household_id = %s
            """,
            (profile_id, household_id),
        ).fetchone()
        if row is None:
            raise LookupError("household not found")


repository = LedgerRepository()


def normalize_csv_rows(content: str, household_id: str, batch_id: str) -> list[Transaction]:
    reader = csv.DictReader(io.StringIO(content))
    if not reader.fieldnames or not REQUIRED_COLUMNS.issubset(reader.fieldnames):
        raise ValueError(f"CSV requires columns: {', '.join(sorted(REQUIRED_COLUMNS))}")
    return [
        _normalize_row(household_id=household_id, batch_id=batch_id, row=row, row_number=row_number)
        for row_number, row in enumerate(reader, start=2)
    ]


def _normalize_row(*, household_id: str, batch_id: str, row: dict[str, str], row_number: int) -> Transaction:
    try:
        kind = row["type"].strip().upper()
        if kind not in SUPPORTED_TYPES:
            raise ValueError(f"unsupported transaction type {kind!r}")
        quantity = Decimal(row["quantity"])
        price = Decimal(row["price"])
        if quantity <= 0 or price < 0:
            raise ValueError("quantity must be positive and price cannot be negative")
        source_id = row.get("external_id", "").strip() or hashlib.sha256(
            "|".join(f"{key}={value}" for key, value in sorted(row.items())).encode("utf-8")
        ).hexdigest()
        return Transaction(
            id=str(uuid4()),
            household_id=household_id,
            account_id=row["account_id"].strip(),
            symbol=row["symbol"].strip().upper(),
            transaction_date=date.fromisoformat(row["transaction_date"]),
            quantity=quantity,
            price=price,
            market_price=Decimal(row["market_price"]) if row.get("market_price") else None,
            transaction_type=kind,
            cost_basis=Decimal(row.get("cost_basis") or quantity * price),
            lot_id=row.get("lot_id", "").strip() or source_id,
            source_id=source_id,
            batch_id=batch_id,
        )
    except (KeyError, InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid row {row_number}: {exc}") from exc


__all__ = [
    "CAPABILITIES",
    "ImportBatch",
    "LedgerStore",
    "REQUIRED_COLUMNS",
    "SUPPORTED_TYPES",
    "Transaction",
    "normalize_csv_rows",
    "repository",
]
