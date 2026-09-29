"""Canonical ledger persistence, normalization, and household-scoped platform storage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.session import get_connection
from app.domain.models import (
    Account,
    AuditEvent,
    ConnectorLink,
    HouseholdPerson,
    HouseholdSummary,
    ImportBatch,
    MemoryEntry,
    PortfolioPolicy,
    PortfolioSnapshot,
    Profile,
    StoredProfileCredentials,
    SyncRun,
    Transaction,
)
from app.services.raw_uploads import raw_upload_store
from app.services.secrets.provider import secret_provider_registry


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
    "plaid": {
        "balances": True,
        "positions": True,
        "transactions": False,
        "tax_lots": False,
        "historical_import": False,
        "incremental_sync": False,
    },
}
SUPPORTED_TYPES = {"BUY", "SELL", "TRANSFER_IN"}
REQUIRED_COLUMNS = {"account_id", "symbol", "transaction_date", "quantity", "type"}
ALLOWED_CONNECTOR_STATUSES = {"pending", "active", "import_only", "error"}
ALLOWED_SYNC_TRIGGERS = {"import", "manual", "scheduled", "ai_tool"}
ALLOWED_SYNC_STATUSES = {"pending", "succeeded", "skipped", "failed", "stale"}
ALLOWED_MEMORY_TYPES = {"goal", "constraint", "preference", "reconciliation_note"}
ALLOWED_MEMORY_IMPORTANCE = {"low", "medium", "high"}
CSV_HEADER_ALIASES = {
    "account_id": {
        "account",
        "account id",
        "account number",
        "account_number",
        "account_id",
        "account#",
        "acct",
        "acct#",
        "acct number",
        "portfolio",
        "portfolio id",
        "portfolio_id",
    },
    "symbol": {"symbol", "ticker", "ticker symbol", "security", "security symbol"},
    "transaction_date": {
        "activity date",
        "date",
        "date/time",
        "posted date",
        "settle date",
        "trade date",
        "transaction date",
        "transaction_date",
    },
    "quantity": {"quantity", "qty", "share quantity", "shares", "units"},
    "price": {"price", "price/share", "share price", "t. price", "trade price", "unit price"},
    "amount": {"amount", "gross amount", "net amount", "principal", "proceeds", "total amount", "trade amount"},
    "type": {"action", "activity type", "buy/sell", "buy sell", "transaction type", "type"},
    "market_price": {"current price", "last price", "market price", "market_price"},
    "cost_basis": {"cost basis", "cost_basis"},
    "lot_id": {"lot", "lot id", "lot_id", "tax lot", "tax lot id"},
    "external_id": {
        "activity id",
        "confirmation number",
        "external id",
        "external_id",
        "reference id",
        "trade id",
        "transaction id",
    },
}
TRANSACTION_TYPE_ALIASES = {
    "BUY": "BUY",
    "BOUGHT": "BUY",
    "PURCHASE": "BUY",
    "REINVEST": "BUY",
    "REINVESTMENT": "BUY",
    "DIVIDEND REINVESTMENT": "BUY",
    "SELL": "SELL",
    "SOLD": "SELL",
    "SALE": "SELL",
    "TRANSFER IN": "TRANSFER_IN",
    "TRANSFER_IN": "TRANSFER_IN",
    "INCOMING TRANSFER": "TRANSFER_IN",
    "JOURNAL IN": "TRANSFER_IN",
    "DELIVER IN": "TRANSFER_IN",
}
DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d-%b-%Y",
    "%d-%b-%y",
    "%b %d %Y",
    "%b %d, %Y",
)


class LedgerStore:
    """Legacy in-memory harness retained for narrow unit tests."""

    def __init__(self) -> None:
        self.batches: dict[str, ImportBatch] = {}
        self.transactions: dict[str, Transaction] = {}
        self.event_ids: set[tuple[str, str, str]] = set()

    def import_csv(self, household_id: str, household_person_id: str, connector: str, content: str) -> ImportBatch:
        if connector not in CAPABILITIES:
            raise ValueError(f"unsupported connector: {connector}")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        existing = next(
            (
                batch
                for batch in self.batches.values()
                if batch.household_id == household_id
                and batch.household_person_id == household_person_id
                and batch.connector == connector
                and batch.content_hash == content_hash
            ),
            None,
        )
        if existing:
            return existing
        batch_id = str(uuid4())
        imported = 0
        for transaction in normalize_csv_rows(content, household_id, household_person_id, batch_id):
            event_key = (household_id, household_person_id, transaction.source_id)
            if event_key in self.event_ids:
                continue
            self.event_ids.add(event_key)
            self.transactions[transaction.id] = transaction
            imported += 1
        batch = ImportBatch(
            id=batch_id,
            household_id=household_id,
            household_person_id=household_person_id,
            connector=connector,
            content_hash=content_hash,
            imported_at=datetime.now(timezone.utc),
            raw_upload_path="memory://raw-upload",
            row_count=imported,
        )
        self.batches[batch.id] = batch
        return batch

    def for_household(self, household_id: str, household_person_id: str | None = None) -> list[Transaction]:
        batch_ids = {
            batch.id
            for batch in self.batches.values()
            if batch.household_id == household_id
            and (household_person_id is None or batch.household_person_id == household_person_id)
        }
        return [transaction for transaction in self.transactions.values() if transaction.batch_id in batch_ids]


class LedgerRepository:
    def register_profile(self, email: str, full_name: str, password_hash: str, household_name: str) -> Profile:
        if not full_name.strip():
            raise ValueError("full_name is required")
        if not household_name.strip():
            raise ValueError("household_name is required")
        profile_id = str(uuid4())
        household_id = str(uuid4())
        person_id = str(uuid4())
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
            self._insert_household_person(
                connection,
                person_id=person_id,
                household_id=household_id,
                full_name=full_name.strip(),
                linked_profile_id=profile_id,
                created_by_profile_id=profile_id,
                created_at=now,
            )
            self._insert_audit_event(
                connection,
                profile_id=profile_id,
                household_id=household_id,
                event_type="household.created",
                entity_type="household",
                entity_id=household_id,
                details={"name": household_name.strip(), "linked_person_id": person_id},
                created_at=now,
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
                SELECT h.id, h.name, hm.role, h.created_at, hp.id AS linked_person_id
                FROM households h
                JOIN household_memberships hm ON hm.household_id = h.id
                LEFT JOIN household_people hp
                    ON hp.household_id = h.id
                   AND hp.linked_profile_id = hm.profile_id
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
        person_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            profile_row = self._require_profile_row(connection, profile_id)
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
            self._insert_household_person(
                connection,
                person_id=person_id,
                household_id=household_id,
                full_name=profile_row["full_name"],
                linked_profile_id=profile_id,
                created_by_profile_id=profile_id,
                created_at=now,
            )
            self._insert_audit_event(
                connection,
                profile_id=profile_id,
                household_id=household_id,
                event_type="household.created",
                entity_type="household",
                entity_id=household_id,
                details={"name": name.strip(), "linked_person_id": person_id},
                created_at=now,
            )
            connection.commit()
        return HouseholdSummary(
            id=household_id,
            name=name.strip(),
            role="owner",
            created_at=now,
            linked_person_id=person_id,
        )

    def list_household_people(self, profile_id: str, household_id: str) -> list[HouseholdPerson]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at,
                       linked_profile_id = %s AS is_linked_profile
                FROM household_people
                WHERE household_id = %s
                ORDER BY created_at, id
                """,
                (profile_id, household_id),
            ).fetchall()
        return [self._map_household_person(row) for row in rows]

    def get_household_person(self, profile_id: str, household_id: str, household_person_id: str) -> HouseholdPerson:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = self._require_household_person(connection, household_id, household_person_id, profile_id=profile_id)
        return self._map_household_person(row)

    def create_household_person(self, profile_id: str, household_id: str, full_name: str) -> HouseholdPerson:
        if not full_name.strip():
            raise ValueError("full_name is required")
        person_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = self._insert_household_person(
                connection,
                person_id=person_id,
                household_id=household_id,
                full_name=full_name.strip(),
                linked_profile_id=None,
                created_by_profile_id=profile_id,
                created_at=now,
                profile_id_for_flag=profile_id,
            )
            self._insert_audit_event(
                connection,
                profile_id=profile_id,
                household_id=household_id,
                event_type="person.created",
                entity_type="household_person",
                entity_id=person_id,
                details={"full_name": full_name.strip()},
                created_at=now,
            )
            connection.commit()
        return self._map_household_person(row)

    def import_csv(
        self,
        profile_id: str,
        household_id: str,
        household_person_id: str,
        connector: str,
        content: str,
    ) -> tuple[ImportBatch, bool]:
        if connector not in CAPABILITIES:
            raise ValueError(f"unsupported connector: {connector}")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        batch_id = str(uuid4())
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            self._require_household_person(connection, household_id, household_person_id)
            normalized = normalize_csv_rows(content, household_id, household_person_id, batch_id)
            existing = connection.execute(
                """
                SELECT ib.id, ib.household_id, ib.household_person_id, ib.connector, ib.content_hash,
                       ib.imported_at, ib.raw_upload_path, ib.row_count, hp.full_name AS person_name
                FROM import_batches ib
                JOIN household_people hp ON hp.id = ib.household_person_id
                WHERE ib.household_id = %s
                  AND ib.household_person_id = %s
                  AND ib.connector = %s
                  AND ib.content_hash = %s
                """,
                (household_id, household_person_id, connector, content_hash),
            ).fetchone()
            if existing is not None:
                return self._map_import_batch(existing), True
            raw_upload_path = raw_upload_store.store(household_id, household_person_id, connector, content_hash, content)
            created = connection.execute(
                """
                INSERT INTO import_batches (
                    id, household_id, household_person_id, connector, content_hash,
                    raw_upload_path, row_count, imported_at, created_by_profile_id
                )
                VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s)
                ON CONFLICT (household_id, household_person_id, connector, content_hash) DO NOTHING
                RETURNING id, household_id, household_person_id, connector, content_hash,
                          imported_at, raw_upload_path, row_count
                """,
                (
                    batch_id,
                    household_id,
                    household_person_id,
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
                    SELECT ib.id, ib.household_id, ib.household_person_id, ib.connector, ib.content_hash,
                           ib.imported_at, ib.raw_upload_path, ib.row_count, hp.full_name AS person_name
                    FROM import_batches ib
                    JOIN household_people hp ON hp.id = ib.household_person_id
                    WHERE ib.household_id = %s
                      AND ib.household_person_id = %s
                      AND ib.connector = %s
                      AND ib.content_hash = %s
                    """,
                    (household_id, household_person_id, connector, content_hash),
                ).fetchone()
                return self._map_import_batch(existing), True
            inserted = 0
            for transaction in normalized:
                account_row = connection.execute(
                    """
                    INSERT INTO accounts (
                        id, household_id, household_person_id, external_account_id,
                        connector, display_name, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (household_id, household_person_id, external_account_id)
                    DO UPDATE SET connector = EXCLUDED.connector, display_name = EXCLUDED.display_name
                    RETURNING id
                    """,
                    (
                        str(uuid4()),
                        household_id,
                        household_person_id,
                        transaction.account_id,
                        connector,
                        transaction.account_id,
                        datetime.now(timezone.utc),
                    ),
                ).fetchone()
                inserted_row = connection.execute(
                    """
                    INSERT INTO transactions (
                        id, household_id, household_person_id, account_id, account_external_id, symbol,
                        transaction_date, quantity, price, market_price, transaction_type,
                        cost_basis, lot_id, source_id, batch_id
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (household_id, household_person_id, source_id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        transaction.id,
                        transaction.household_id,
                        transaction.household_person_id,
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
                UPDATE import_batches ib
                SET row_count = %s
                FROM household_people hp
                WHERE ib.id = %s AND hp.id = ib.household_person_id
                RETURNING ib.id, ib.household_id, ib.household_person_id, ib.connector, ib.content_hash,
                          ib.imported_at, ib.raw_upload_path, ib.row_count, hp.full_name AS person_name
                """,
                (inserted, batch_id),
            ).fetchone()
            connection.commit()
        return self._map_import_batch(batch_row), False

    def for_household(
        self,
        profile_id: str,
        household_id: str,
        household_person_id: str | None = None,
    ) -> list[Transaction]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            if household_person_id is not None:
                self._require_household_person(connection, household_id, household_person_id)
            rows = connection.execute(
                f"""
                SELECT t.id, t.household_id, t.household_person_id, t.account_external_id AS account_id,
                       t.symbol, t.transaction_date, t.quantity, t.price, t.market_price,
                       t.transaction_type, t.cost_basis, t.lot_id, t.source_id, t.batch_id,
                       hp.full_name AS person_name
                FROM transactions t
                JOIN household_people hp ON hp.id = t.household_person_id
                WHERE t.household_id = %s
                {"AND t.household_person_id = %s" if household_person_id is not None else ""}
                ORDER BY t.transaction_date, t.id
                """,
                (household_id, household_person_id) if household_person_id is not None else (household_id,),
            ).fetchall()
        return [self._map_transaction(row) for row in rows]

    def list_import_batches(
        self,
        profile_id: str,
        household_id: str,
        household_person_id: str | None = None,
    ) -> list[ImportBatch]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            if household_person_id is not None:
                self._require_household_person(connection, household_id, household_person_id)
            rows = connection.execute(
                f"""
                SELECT ib.id, ib.household_id, ib.household_person_id, ib.connector, ib.content_hash,
                       ib.imported_at, ib.raw_upload_path, ib.row_count, hp.full_name AS person_name
                FROM import_batches ib
                JOIN household_people hp ON hp.id = ib.household_person_id
                WHERE ib.household_id = %s
                {"AND ib.household_person_id = %s" if household_person_id is not None else ""}
                ORDER BY ib.imported_at DESC, ib.id DESC
                """,
                (household_id, household_person_id) if household_person_id is not None else (household_id,),
            ).fetchall()
        return [self._map_import_batch(row) for row in rows]

    def list_accounts(
        self,
        profile_id: str,
        household_id: str,
        household_person_id: str | None = None,
    ) -> list[Account]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            if household_person_id is not None:
                self._require_household_person(connection, household_id, household_person_id)
            rows = connection.execute(
                f"""
                SELECT a.id, a.household_id, a.household_person_id, a.external_account_id,
                       a.connector, a.display_name, a.created_at, hp.full_name AS person_name
                FROM accounts a
                JOIN household_people hp ON hp.id = a.household_person_id
                WHERE a.household_id = %s
                {"AND a.household_person_id = %s" if household_person_id is not None else ""}
                ORDER BY hp.full_name, a.display_name, a.id
                """,
                (household_id, household_person_id) if household_person_id is not None else (household_id,),
            ).fetchall()
        return [self._map_account(row) for row in rows]

    def list_transactions(
        self,
        profile_id: str,
        household_id: str,
        household_person_id: str | None = None,
        *,
        limit: int,
    ) -> list[Transaction]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            if household_person_id is not None:
                self._require_household_person(connection, household_id, household_person_id)
            rows = connection.execute(
                f"""
                SELECT t.id, t.household_id, t.household_person_id, t.account_external_id AS account_id,
                       t.symbol, t.transaction_date, t.quantity, t.price, t.market_price,
                       t.transaction_type, t.cost_basis, t.lot_id, t.source_id, t.batch_id,
                       hp.full_name AS person_name
                FROM transactions t
                JOIN household_people hp ON hp.id = t.household_person_id
                WHERE t.household_id = %s
                {"AND t.household_person_id = %s" if household_person_id is not None else ""}
                ORDER BY t.transaction_date DESC, t.id DESC
                LIMIT %s
                """,
                (household_id, household_person_id, limit) if household_person_id is not None else (household_id, limit),
            ).fetchall()
        return [self._map_transaction(row) for row in rows]

    def upsert_connector_link(
        self,
        *,
        profile_id: str,
        household_id: str,
        household_person_id: str,
        connector: str,
        display_name: str,
        status: str,
        secret_provider: str,
        secret_reference: str | None,
        external_reference: str | None,
        capabilities: dict[str, Any],
    ) -> ConnectorLink:
        if connector not in CAPABILITIES:
            raise ValueError(f"unsupported connector: {connector}")
        if status not in ALLOWED_CONNECTOR_STATUSES:
            raise ValueError(f"unsupported connector status: {status}")
        if not display_name.strip():
            raise ValueError("display_name is required")
        secret_provider_registry.get(secret_provider)
        link_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            self._require_household_person(connection, household_id, household_person_id)
            row = connection.execute(
                """
                INSERT INTO connector_links (
                    id, household_id, household_person_id, connector, display_name, status, secret_provider,
                    secret_reference, external_reference, capabilities, created_by_profile_id,
                    last_synced_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
                ON CONFLICT (household_id, household_person_id, connector)
                DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    status = EXCLUDED.status,
                    secret_provider = EXCLUDED.secret_provider,
                    secret_reference = EXCLUDED.secret_reference,
                    external_reference = EXCLUDED.external_reference,
                    capabilities = EXCLUDED.capabilities,
                    updated_at = EXCLUDED.updated_at
                RETURNING id
                """,
                (
                    link_id,
                    household_id,
                    household_person_id,
                    connector,
                    display_name.strip(),
                    status,
                    secret_provider,
                    secret_reference,
                    external_reference,
                    Jsonb(_jsonable(capabilities)),
                    profile_id,
                    now,
                    now,
                ),
            ).fetchone()
            mapped = self._fetch_connector_link_row(connection, row["id"], profile_id)
            connection.commit()
        return self._map_connector_link(mapped)

    def list_connector_links(self, profile_id: str, household_id: str) -> list[ConnectorLink]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT cl.id, cl.household_id, cl.household_person_id, cl.connector, cl.display_name,
                       cl.status, cl.capabilities, cl.secret_provider, cl.secret_reference,
                       cl.external_reference, cl.created_by_profile_id, cl.last_synced_at,
                       cl.created_at, cl.updated_at, hp.full_name AS person_name
                FROM connector_links cl
                JOIN household_people hp ON hp.id = cl.household_person_id
                WHERE cl.household_id = %s
                ORDER BY cl.updated_at DESC, cl.id DESC
                """,
                (household_id,),
            ).fetchall()
        return [self._map_connector_link(row) for row in rows]

    def save_plaid_access_token(self, *, connector_link_id: str, item_id: str, access_token: str) -> None:
        from app.services.secrets.crypto import EncryptedPayloadCodec

        encrypted_token = EncryptedPayloadCodec().encrypt_text(
            access_token,
            aad={"connector": "plaid", "connector_link_id": connector_link_id, "item_id": item_id},
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO plaid_items (connector_link_id, item_id, encrypted_access_token, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(), NOW())
                ON CONFLICT (connector_link_id) DO UPDATE SET
                    item_id = EXCLUDED.item_id,
                    encrypted_access_token = EXCLUDED.encrypted_access_token,
                    updated_at = NOW()
                """,
                (connector_link_id, item_id, encrypted_token),
            )
            connection.commit()

    def record_sync_run(
        self,
        *,
        profile_id: str,
        household_id: str,
        household_person_id: str | None,
        connector_link_id: str | None,
        trigger: str,
        status: str,
        summary: str,
        stats: dict[str, Any],
    ) -> SyncRun:
        if trigger not in ALLOWED_SYNC_TRIGGERS:
            raise ValueError(f"unsupported sync trigger: {trigger}")
        if status not in ALLOWED_SYNC_STATUSES:
            raise ValueError(f"unsupported sync status: {status}")
        if not summary.strip():
            raise ValueError("summary is required")
        sync_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            if household_person_id is not None:
                self._require_household_person(connection, household_id, household_person_id)
            row = connection.execute(
                """
                INSERT INTO sync_runs (
                    id, household_id, household_person_id, connector_link_id, trigger, status,
                    summary, stats, created_by_profile_id, started_at, completed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, household_id, household_person_id, connector_link_id, trigger, status,
                          summary, stats, created_by_profile_id, started_at, completed_at
                """,
                (
                    sync_id,
                    household_id,
                    household_person_id,
                    connector_link_id,
                    trigger,
                    status,
                    summary.strip(),
                    Jsonb(_jsonable(stats)),
                    profile_id,
                    now,
                    now if status != "pending" else None,
                ),
            ).fetchone()
            if connector_link_id and status in {"succeeded", "skipped", "stale", "failed"}:
                connection.execute(
                    """
                    UPDATE connector_links
                    SET last_synced_at = %s,
                        updated_at = %s,
                        status = CASE WHEN %s = 'failed' THEN 'error' ELSE status END
                    WHERE id = %s
                    """,
                    (now, now, status, connector_link_id),
                )
            mapped = self._fetch_sync_run_row(connection, sync_id)
            connection.commit()
        return self._map_sync_run(mapped)

    def list_sync_runs(self, profile_id: str, household_id: str, *, limit: int) -> list[SyncRun]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT sr.id, sr.household_id, sr.household_person_id, sr.connector_link_id, sr.trigger,
                       sr.status, sr.summary, sr.stats, sr.created_by_profile_id, sr.started_at,
                       sr.completed_at, hp.full_name AS person_name
                FROM sync_runs sr
                LEFT JOIN household_people hp ON hp.id = sr.household_person_id
                WHERE sr.household_id = %s
                ORDER BY sr.started_at DESC, sr.id DESC
                LIMIT %s
                """,
                (household_id, limit),
            ).fetchall()
        return [self._map_sync_run(row) for row in rows]

    def save_policy(
        self,
        *,
        profile_id: str,
        household_id: str,
        name: str,
        target_allocations: dict[str, Any],
        rebalance_threshold_pct: Any,
        cash_reserve_target_pct: Any,
        max_single_position_pct: Any = None,
        notes: str | None = None,
    ) -> PortfolioPolicy:
        if not name.strip():
            raise ValueError("name is required")
        normalized_targets = _normalize_target_allocations(target_allocations)
        rebalance_threshold = _normalize_percentage(rebalance_threshold_pct, field_name="rebalance_threshold_pct")
        cash_reserve_target = _normalize_percentage(cash_reserve_target_pct, field_name="cash_reserve_target_pct")
        max_single_position = (
            None
            if max_single_position_pct in {None, ""}
            else _normalize_percentage(max_single_position_pct, field_name="max_single_position_pct")
        )
        if sum((Decimal(value) for value in normalized_targets.values()), Decimal("0")) + Decimal(cash_reserve_target) > Decimal("100"):
            raise ValueError("target allocations plus cash reserve must not exceed 100")
        policy_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = connection.execute(
                """
                INSERT INTO portfolio_policies (
                    id, household_id, name, target_allocations, rebalance_threshold_pct,
                    cash_reserve_target_pct, max_single_position_pct, notes,
                    created_by_profile_id, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, household_id, name, target_allocations, rebalance_threshold_pct,
                          cash_reserve_target_pct, max_single_position_pct, notes,
                          created_by_profile_id, created_at
                """,
                (
                    policy_id,
                    household_id,
                    name.strip(),
                    Jsonb(_jsonable(normalized_targets)),
                    Decimal(rebalance_threshold),
                    Decimal(cash_reserve_target),
                    Decimal(max_single_position) if max_single_position is not None else None,
                    notes.strip() if notes else None,
                    profile_id,
                    now,
                ),
            ).fetchone()
            connection.commit()
        return self._map_policy(row)

    def list_policies(self, profile_id: str, household_id: str, *, limit: int) -> list[PortfolioPolicy]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, name, target_allocations, rebalance_threshold_pct,
                       cash_reserve_target_pct, max_single_position_pct, notes,
                       created_by_profile_id, created_at
                FROM portfolio_policies
                WHERE household_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (household_id, limit),
            ).fetchall()
        return [self._map_policy(row) for row in rows]

    def get_latest_policy(self, profile_id: str, household_id: str) -> PortfolioPolicy | None:
        policies = self.list_policies(profile_id, household_id, limit=1)
        return policies[0] if policies else None

    def add_memory_entry(
        self,
        *,
        profile_id: str,
        household_id: str,
        entry_type: str,
        content: str,
        labels: list[str],
        importance: str,
    ) -> MemoryEntry:
        normalized_type = entry_type.strip().lower()
        normalized_importance = importance.strip().lower()
        if normalized_type not in ALLOWED_MEMORY_TYPES:
            raise ValueError(f"unsupported memory entry type: {entry_type}")
        if normalized_importance not in ALLOWED_MEMORY_IMPORTANCE:
            raise ValueError(f"unsupported memory importance: {importance}")
        if not content.strip():
            raise ValueError("content is required")
        entry_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = connection.execute(
                """
                INSERT INTO memory_entries (
                    id, household_id, entry_type, content, labels, importance, created_by_profile_id, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, household_id, entry_type, content, labels, importance,
                          created_by_profile_id, created_at
                """,
                (
                    entry_id,
                    household_id,
                    normalized_type,
                    content.strip(),
                    Jsonb(_jsonable(_normalize_labels(labels))),
                    normalized_importance,
                    profile_id,
                    now,
                ),
            ).fetchone()
            connection.commit()
        return self._map_memory_entry(row)

    def list_memory_entries(self, profile_id: str, household_id: str, *, limit: int) -> list[MemoryEntry]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, entry_type, content, labels, importance,
                       created_by_profile_id, created_at
                FROM memory_entries
                WHERE household_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (household_id, limit),
            ).fetchall()
        return [self._map_memory_entry(row) for row in rows]

    def create_snapshot(
        self,
        *,
        profile_id: str,
        household_id: str,
        snapshot_type: str,
        source: str,
        freshness: str,
        sync_status: str,
        payload: dict[str, Any],
        based_on_sync_run_id: str | None,
    ) -> PortfolioSnapshot:
        if not snapshot_type.strip():
            raise ValueError("snapshot_type is required")
        snapshot_id = str(uuid4())
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = connection.execute(
                """
                INSERT INTO portfolio_snapshots (
                    id, household_id, snapshot_type, source, freshness, sync_status,
                    payload, based_on_sync_run_id, created_by_profile_id, created_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, household_id, snapshot_type, source, freshness, sync_status,
                          payload, based_on_sync_run_id, created_by_profile_id, created_at
                """,
                (
                    snapshot_id,
                    household_id,
                    snapshot_type.strip(),
                    source,
                    freshness,
                    sync_status,
                    Jsonb(_jsonable(payload)),
                    based_on_sync_run_id,
                    profile_id,
                    now,
                ),
            ).fetchone()
            connection.commit()
        return self._map_snapshot(row)

    def list_snapshots(self, profile_id: str, household_id: str, *, limit: int) -> list[PortfolioSnapshot]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, snapshot_type, source, freshness, sync_status,
                       payload, based_on_sync_run_id, created_by_profile_id, created_at
                FROM portfolio_snapshots
                WHERE household_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (household_id, limit),
            ).fetchall()
        return [self._map_snapshot(row) for row in rows]

    def get_latest_snapshot(self, profile_id: str, household_id: str) -> PortfolioSnapshot | None:
        snapshots = self.list_snapshots(profile_id, household_id, limit=1)
        return snapshots[0] if snapshots else None

    def record_audit_event(
        self,
        *,
        profile_id: str,
        household_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any],
    ) -> AuditEvent:
        now = datetime.now(timezone.utc)
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = self._insert_audit_event(
                connection,
                profile_id=profile_id,
                household_id=household_id,
                event_type=event_type,
                entity_type=entity_type,
                entity_id=entity_id,
                details=details,
                created_at=now,
            )
            connection.commit()
        return self._map_audit_event(row)

    def list_audit_events(self, profile_id: str, household_id: str, *, limit: int) -> list[AuditEvent]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, event_type, entity_type, entity_id, details,
                       created_by_profile_id, created_at
                FROM audit_events
                WHERE household_id = %s
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (household_id, limit),
            ).fetchall()
        return [self._map_audit_event(row) for row in rows]

    def snapshot_metadata(self, profile_id: str, household_id: str) -> dict:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM import_batches WHERE household_id = %(household_id)s) AS batch_count,
                    (SELECT MAX(imported_at) FROM import_batches WHERE household_id = %(household_id)s) AS latest_imported_at,
                    (SELECT MAX(created_at) FROM portfolio_snapshots WHERE household_id = %(household_id)s) AS latest_snapshot_at,
                    (SELECT MAX(completed_at) FROM sync_runs WHERE household_id = %(household_id)s) AS latest_sync_completed_at,
                    (
                        SELECT status
                        FROM sync_runs
                        WHERE household_id = %(household_id)s
                        ORDER BY started_at DESC, id DESC
                        LIMIT 1
                    ) AS latest_sync_status
                """,
                {"household_id": household_id},
            ).fetchone()
        now = datetime.now(timezone.utc)
        latest_activity = row["latest_sync_completed_at"] or row["latest_imported_at"] or row["latest_snapshot_at"]
        freshness = "unavailable"
        if latest_activity is not None:
            freshness = "current" if now - latest_activity <= timedelta(hours=24) else "stale"
        batch_count = int(row["batch_count"])
        return {
            "source": "portfolio_snapshot" if row["latest_snapshot_at"] else "canonical_ledger_postgresql",
            "freshness": freshness,
            "sync_status": row["latest_sync_status"] or ("success" if batch_count else "never_synced"),
            "calculation_version": "ai-ready-mvp-2",
            "latest_imported_at": row["latest_imported_at"],
            "latest_snapshot_at": row["latest_snapshot_at"],
            "latest_sync_completed_at": row["latest_sync_completed_at"],
        }

    @staticmethod
    def _map_profile(row: dict) -> Profile:
        return Profile(id=row["id"], email=row["email"], full_name=row["full_name"], created_at=row["created_at"])

    @staticmethod
    def _map_household(row: dict) -> HouseholdSummary:
        return HouseholdSummary(
            id=row["id"],
            name=row["name"],
            role=row["role"],
            created_at=row["created_at"],
            linked_person_id=row.get("linked_person_id"),
        )

    @staticmethod
    def _map_household_person(row: dict) -> HouseholdPerson:
        return HouseholdPerson(
            id=row["id"],
            household_id=row["household_id"],
            full_name=row["full_name"],
            linked_profile_id=row["linked_profile_id"],
            created_by_profile_id=row["created_by_profile_id"],
            created_at=row["created_at"],
            is_linked_profile=bool(row.get("is_linked_profile")),
        )

    @staticmethod
    def _map_account(row: dict) -> Account:
        return Account(
            id=row["id"],
            household_id=row["household_id"],
            household_person_id=row["household_person_id"],
            external_account_id=row["external_account_id"],
            connector=row["connector"],
            display_name=row["display_name"],
            created_at=row["created_at"],
            person_name=row.get("person_name"),
        )

    @staticmethod
    def _map_import_batch(row: dict) -> ImportBatch:
        return ImportBatch(
            id=row["id"],
            household_id=row["household_id"],
            household_person_id=row["household_person_id"],
            connector=row["connector"],
            content_hash=row["content_hash"],
            imported_at=row["imported_at"],
            raw_upload_path=row["raw_upload_path"],
            row_count=row["row_count"],
            person_name=row.get("person_name"),
        )

    @staticmethod
    def _map_transaction(row: dict) -> Transaction:
        return Transaction(
            id=row["id"],
            household_id=row["household_id"],
            household_person_id=row["household_person_id"],
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
            person_name=row.get("person_name"),
        )

    @staticmethod
    def _map_connector_link(row: dict) -> ConnectorLink:
        return ConnectorLink(
            id=row["id"],
            household_id=row["household_id"],
            household_person_id=row["household_person_id"],
            connector=row["connector"],
            display_name=row["display_name"],
            status=row["status"],
            capabilities=_coerce_json(row["capabilities"]),
            secret_provider=row["secret_provider"],
            secret_reference=row["secret_reference"],
            external_reference=row["external_reference"],
            created_by_profile_id=row["created_by_profile_id"],
            last_synced_at=row["last_synced_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            person_name=row.get("person_name"),
        )

    @staticmethod
    def _map_sync_run(row: dict) -> SyncRun:
        return SyncRun(
            id=row["id"],
            household_id=row["household_id"],
            household_person_id=row["household_person_id"],
            connector_link_id=row["connector_link_id"],
            trigger=row["trigger"],
            status=row["status"],
            summary=row["summary"],
            stats=_coerce_json(row["stats"]),
            created_by_profile_id=row["created_by_profile_id"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            person_name=row.get("person_name"),
        )

    @staticmethod
    def _map_policy(row: dict) -> PortfolioPolicy:
        return PortfolioPolicy(
            id=row["id"],
            household_id=row["household_id"],
            name=row["name"],
            target_allocations={key: str(value) for key, value in _coerce_json(row["target_allocations"]).items()},
            rebalance_threshold_pct=str(row["rebalance_threshold_pct"]),
            cash_reserve_target_pct=str(row["cash_reserve_target_pct"]),
            max_single_position_pct=(None if row["max_single_position_pct"] is None else str(row["max_single_position_pct"])),
            notes=row["notes"],
            created_by_profile_id=row["created_by_profile_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _map_memory_entry(row: dict) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            household_id=row["household_id"],
            entry_type=row["entry_type"],
            content=row["content"],
            labels=[str(value) for value in _coerce_json(row["labels"])],
            importance=row["importance"],
            created_by_profile_id=row["created_by_profile_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _map_snapshot(row: dict) -> PortfolioSnapshot:
        return PortfolioSnapshot(
            id=row["id"],
            household_id=row["household_id"],
            snapshot_type=row["snapshot_type"],
            source=row["source"],
            freshness=row["freshness"],
            sync_status=row["sync_status"],
            payload=_coerce_json(row["payload"]),
            based_on_sync_run_id=row["based_on_sync_run_id"],
            created_by_profile_id=row["created_by_profile_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _map_audit_event(row: dict) -> AuditEvent:
        return AuditEvent(
            id=row["id"],
            household_id=row["household_id"],
            event_type=row["event_type"],
            entity_type=row["entity_type"],
            entity_id=row["entity_id"],
            details=_coerce_json(row["details"]),
            created_by_profile_id=row["created_by_profile_id"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _insert_audit_event(
        connection,
        *,
        profile_id: str,
        household_id: str,
        event_type: str,
        entity_type: str,
        entity_id: str,
        details: dict[str, Any],
        created_at: datetime,
    ):
        return connection.execute(
            """
            INSERT INTO audit_events (
                id, household_id, event_type, entity_type, entity_id, details,
                created_by_profile_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, household_id, event_type, entity_type, entity_id, details,
                      created_by_profile_id, created_at
            """,
            (
                str(uuid4()),
                household_id,
                event_type,
                entity_type,
                entity_id,
                Jsonb(_jsonable(details)),
                profile_id,
                created_at,
            ),
        ).fetchone()

    @staticmethod
    def _require_profile_row(connection, profile_id: str) -> dict:
        row = connection.execute(
            "SELECT id, full_name FROM profiles WHERE id = %s",
            (profile_id,),
        ).fetchone()
        if row is None:
            raise LookupError("profile not found")
        return row

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

    @staticmethod
    def _require_household_person(
        connection,
        household_id: str,
        household_person_id: str,
        *,
        profile_id: str | None = None,
    ) -> dict:
        if profile_id is None:
            row = connection.execute(
                """
                SELECT id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at, FALSE AS is_linked_profile
                FROM household_people
                WHERE id = %s AND household_id = %s
                """,
                (household_person_id, household_id),
            ).fetchone()
        else:
            row = connection.execute(
                """
                SELECT id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at,
                       linked_profile_id = %s AS is_linked_profile
                FROM household_people
                WHERE id = %s AND household_id = %s
                """,
                (profile_id, household_person_id, household_id),
            ).fetchone()
        if row is None:
            raise LookupError("person not found")
        return row

    @staticmethod
    def _insert_household_person(
        connection,
        *,
        person_id: str,
        household_id: str,
        full_name: str,
        linked_profile_id: str | None,
        created_by_profile_id: str,
        created_at: datetime,
        profile_id_for_flag: str | None = None,
    ) -> dict:
        return connection.execute(
            """
            INSERT INTO household_people (
                id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at,
                      linked_profile_id = %s AS is_linked_profile
            """,
            (
                person_id,
                household_id,
                full_name,
                linked_profile_id,
                created_by_profile_id,
                created_at,
                profile_id_for_flag or linked_profile_id,
            ),
        ).fetchone()

    @staticmethod
    def _fetch_connector_link_row(connection, connector_link_id: str, profile_id: str) -> dict:
        return connection.execute(
            """
            SELECT cl.id, cl.household_id, cl.household_person_id, cl.connector, cl.display_name,
                   cl.status, cl.capabilities, cl.secret_provider, cl.secret_reference,
                   cl.external_reference, cl.created_by_profile_id, cl.last_synced_at,
                   cl.created_at, cl.updated_at, hp.full_name AS person_name
            FROM connector_links cl
            JOIN household_people hp ON hp.id = cl.household_person_id
            WHERE cl.id = %s
            """,
            (connector_link_id,),
        ).fetchone()

    @staticmethod
    def _fetch_sync_run_row(connection, sync_run_id: str) -> dict:
        return connection.execute(
            """
            SELECT sr.id, sr.household_id, sr.household_person_id, sr.connector_link_id, sr.trigger,
                   sr.status, sr.summary, sr.stats, sr.created_by_profile_id, sr.started_at,
                   sr.completed_at, hp.full_name AS person_name
            FROM sync_runs sr
            LEFT JOIN household_people hp ON hp.id = sr.household_person_id
            WHERE sr.id = %s
            """,
            (sync_run_id,),
        ).fetchone()


repository = LedgerRepository()


def normalize_csv_rows(content: str, household_id: str, household_person_id: str, batch_id: str) -> list[Transaction]:
    fieldnames, source_rows = _read_csv_rows(content)
    header_map = _resolve_headers(fieldnames)
    missing = sorted(REQUIRED_COLUMNS - set(header_map))
    if "price" not in header_map and "amount" not in header_map:
        missing.append("price_or_amount")
    if missing:
        raise ValueError(_format_missing_column_error(fieldnames, missing))
    normalized_rows = []
    for row_number, row in source_rows:
        if _is_blank_row(row):
            continue
        normalized_rows.append(
            _normalize_row(
                household_id=household_id,
                household_person_id=household_person_id,
                batch_id=batch_id,
                row=_canonical_row(row, header_map),
                row_number=row_number,
            )
        )
    return normalized_rows


def _normalize_row(
    *,
    household_id: str,
    household_person_id: str,
    batch_id: str,
    row: dict[str, str],
    row_number: int,
) -> Transaction:
    try:
        account_id = _require_text(row.get("account_id"), field_name="account_id")
        symbol = _require_text(row.get("symbol"), field_name="symbol").upper()
        kind = _normalize_transaction_type(row.get("type"))
        quantity = abs(_parse_decimal(row.get("quantity"), field_name="quantity"))
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        price = _resolve_price(row, quantity)
        if price < 0:
            raise ValueError("price cannot be negative")
        market_price = None if not row.get("market_price") else abs(_parse_decimal(row.get("market_price"), field_name="market_price"))
        cost_basis = (
            abs(_parse_decimal(row.get("cost_basis"), field_name="cost_basis"))
            if row.get("cost_basis")
            else quantity * price
        )
        source_components = {key: value for key, value in row.items() if value not in {None, ""}}
        source_id = row.get("external_id", "").strip() or hashlib.sha256(
            "|".join(f"{key}={value}" for key, value in sorted(source_components.items())).encode("utf-8")
        ).hexdigest()
        return Transaction(
            id=str(uuid4()),
            household_id=household_id,
            household_person_id=household_person_id,
            account_id=account_id,
            symbol=symbol,
            transaction_date=_parse_date(row.get("transaction_date")),
            quantity=quantity,
            price=price,
            market_price=market_price,
            transaction_type=kind,
            cost_basis=cost_basis,
            lot_id=row.get("lot_id", "").strip() or source_id,
            source_id=source_id,
            batch_id=batch_id,
        )
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid row {row_number}: {exc}") from exc


def _read_csv_rows(content: str) -> tuple[list[str], list[tuple[int, dict[str, str]]]]:
    sanitized = content.lstrip("\ufeff")
    sample = sanitized[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = list(csv.reader(io.StringIO(sanitized), dialect=dialect))
    if not table:
        return [], []

    embedded_rows = _extract_embedded_statement_rows(table)
    if embedded_rows:
        fieldnames = list(embedded_rows[0][1])
        return fieldnames, embedded_rows

    fieldnames = table[0]
    return fieldnames, [
        (row_number, {header: value for header, value in zip(fieldnames, row)})
        for row_number, row in enumerate(table[1:], start=2)
    ]


def _extract_embedded_statement_rows(table: list[list[str]]) -> list[tuple[int, dict[str, str]]]:
    """Extract transaction sections from broker statements with per-section headers."""
    account_id = ""
    extracted: list[tuple[int, dict[str, str]]] = []
    for header_row_number, header_row in enumerate(table, start=1):
        if len(header_row) < 3 or header_row[1].strip().lower() != "header":
            continue
        section = header_row[0].strip()
        headers = header_row[2:]
        if {_normalize_header(header) for header in headers} == {"field name", "field value"}:
            for data_row in table[header_row_number:]:
                if len(data_row) < 4 or data_row[0].strip() != section or data_row[1].strip().lower() != "data":
                    if data_row and data_row[0].strip() != section:
                        break
                    continue
                if _normalize_header(data_row[2]) in CSV_HEADER_ALIASES["account_id"]:
                    account_id = data_row[3].strip()
            continue

        header_map = _resolve_headers(headers)
        has_trade_values = (
            REQUIRED_COLUMNS - {"account_id", "type"} <= set(header_map)
            and ("price" in header_map or "amount" in header_map)
        )
        if not has_trade_values:
            continue
        for row_number, data_row in enumerate(table[header_row_number:], start=header_row_number + 1):
            if len(data_row) < 3 or data_row[0].strip() != section or data_row[1].strip().lower() != "data":
                if data_row and data_row[0].strip() != section:
                    break
                continue
            row = {header: value for header, value in zip(headers, data_row[2:])}
            if account_id and not any(_normalize_header(header) in CSV_HEADER_ALIASES["account_id"] for header in headers):
                row["Account Number"] = account_id
            if "type" not in header_map:
                row["Type"] = _transaction_type_from_quantity(row.get(header_map["quantity"]))
            extracted.append((row_number, row))
    return extracted


def _transaction_type_from_quantity(value: str | None) -> str:
    quantity = _parse_decimal(value, field_name="quantity")
    if quantity == 0:
        raise ValueError("quantity must be non-zero to infer transaction type")
    return "BUY" if quantity > 0 else "SELL"


def _resolve_headers(fieldnames: list[str]) -> dict[str, str]:
    header_map: dict[str, str] = {}
    normalized_headers = {_normalize_header(name): name for name in fieldnames if name is not None}
    for canonical, aliases in CSV_HEADER_ALIASES.items():
        for alias in aliases:
            header = normalized_headers.get(_normalize_header(alias))
            if header is not None:
                header_map[canonical] = header
                break
    return header_map


def _canonical_row(row: dict[str, str], header_map: dict[str, str]) -> dict[str, str]:
    return {
        canonical: (row.get(source_header) or "").strip()
        for canonical, source_header in header_map.items()
    }


def _normalize_header(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", name.strip().lower()).strip()


def _is_blank_row(row: dict[str, str]) -> bool:
    return all(not (value or "").strip() for value in row.values())


def _format_missing_column_error(found_headers: list[str], missing: list[str]) -> str:
    labels = []
    for item in missing:
        if item == "price_or_amount":
            labels.append("price (or amount so price can be derived)")
            continue
        labels.append(f"{item} ({', '.join(sorted(CSV_HEADER_ALIASES[item]))})")
    found = ",".join(found_headers) if found_headers else "none"
    return f"CSV is missing required columns: {'; '.join(labels)}. Found headers: {found}"


def _require_text(value: str | None, *, field_name: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")
    return text


def _normalize_transaction_type(value: str | None) -> str:
    text = _require_text(value, field_name="type")
    normalized = TRANSACTION_TYPE_ALIASES.get(text.strip().upper())
    if normalized is None:
        raise ValueError(f"unsupported transaction type {text!r}; supported values include buy, sell, and transfer in")
    return normalized


def _resolve_price(row: dict[str, str], quantity: Decimal) -> Decimal:
    if row.get("price"):
        price = abs(_parse_decimal(row.get("price"), field_name="price"))
    elif row.get("amount"):
        amount = abs(_parse_decimal(row.get("amount"), field_name="amount"))
        if quantity == 0:
            raise ValueError("quantity must be positive to derive price from amount")
        price = amount / quantity
    else:
        raise ValueError("price is required unless amount is supplied")
    return price


def _parse_decimal(value: str | None, *, field_name: str) -> Decimal:
    text = _require_text(value, field_name=field_name)
    negative = text.startswith("(") and text.endswith(")")
    cleaned = text.strip("()").replace("$", "").replace(",", "").replace(" ", "")
    if cleaned in {"", "-", "—", "--", "n/a", "N/A"}:
        raise ValueError(f"{field_name} is required")
    number = Decimal(cleaned)
    return -number if negative else number


def _parse_date(value: str | None) -> date:
    text = _require_text(value, field_name="transaction_date")
    normalized = text.replace("Z", "+00:00")
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(normalized).date()
    except ValueError:
        pass
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(
        "transaction_date must be a valid date in ISO, YYYY/MM/DD, MM/DD/YYYY, or broker-style text formats"
    )


def _normalize_target_allocations(target_allocations: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for symbol, raw_value in target_allocations.items():
        clean_symbol = str(symbol).strip().upper()
        if not clean_symbol:
            raise ValueError("target allocation symbols cannot be blank")
        value = _normalize_percentage(raw_value, field_name=f"target_allocations[{clean_symbol}]")
        normalized[clean_symbol] = value
    return normalized


def _normalize_percentage(raw_value: Any, *, field_name: str) -> str:
    try:
        value = Decimal(str(raw_value))
    except InvalidOperation as exc:
        raise ValueError(f"{field_name} must be a valid decimal value") from exc
    if value < Decimal("0") or value > Decimal("100"):
        raise ValueError(f"{field_name} must be between 0 and 100")
    return _stringify_decimal(value)


def _normalize_labels(labels: list[str]) -> list[str]:
    cleaned = sorted({label.strip().lower() for label in labels if label.strip()})
    return cleaned


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _stringify_decimal(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(inner) for key, inner in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(inner) for inner in value]
    return value


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _stringify_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


__all__ = [
    "CAPABILITIES",
    "CSV_HEADER_ALIASES",
    "ImportBatch",
    "LedgerStore",
    "REQUIRED_COLUMNS",
    "SUPPORTED_TYPES",
    "Transaction",
    "normalize_csv_rows",
    "repository",
]
