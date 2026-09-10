"""Canonical ledger persistence, normalization, and household-scoped platform storage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db.session import get_connection
from app.domain.models import (
    AuditEvent,
    ConnectorLink,
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
}
SUPPORTED_TYPES = {"BUY", "SELL", "TRANSFER_IN"}
REQUIRED_COLUMNS = {"account_id", "symbol", "transaction_date", "quantity", "price", "type"}
ALLOWED_CONNECTOR_STATUSES = {"pending", "active", "import_only", "error"}
ALLOWED_SYNC_TRIGGERS = {"import", "manual", "scheduled", "ai_tool"}
ALLOWED_SYNC_STATUSES = {"pending", "succeeded", "skipped", "failed", "stale"}
ALLOWED_MEMORY_TYPES = {"goal", "constraint", "preference", "reconciliation_note"}
ALLOWED_MEMORY_IMPORTANCE = {"low", "medium", "high"}


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
            self._insert_audit_event(
                connection,
                profile_id=profile_id,
                household_id=household_id,
                event_type="household.created",
                entity_type="household",
                entity_id=household_id,
                details={"name": household_name.strip()},
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
            self._insert_audit_event(
                connection,
                profile_id=profile_id,
                household_id=household_id,
                event_type="household.created",
                entity_type="household",
                entity_id=household_id,
                details={"name": name.strip()},
                created_at=now,
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

    def upsert_connector_link(
        self,
        *,
        profile_id: str,
        household_id: str,
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
            row = connection.execute(
                """
                INSERT INTO connector_links (
                    id, household_id, connector, display_name, status, secret_provider,
                    secret_reference, external_reference, capabilities, created_by_profile_id,
                    last_synced_at, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s)
                ON CONFLICT (household_id, connector)
                DO UPDATE SET
                    display_name = EXCLUDED.display_name,
                    status = EXCLUDED.status,
                    secret_provider = EXCLUDED.secret_provider,
                    secret_reference = EXCLUDED.secret_reference,
                    external_reference = EXCLUDED.external_reference,
                    capabilities = EXCLUDED.capabilities,
                    updated_at = EXCLUDED.updated_at
                RETURNING id, household_id, connector, display_name, status, capabilities,
                          secret_provider, secret_reference, external_reference,
                          created_by_profile_id, last_synced_at, created_at, updated_at
                """,
                (
                    link_id,
                    household_id,
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
            connection.commit()
        return self._map_connector_link(row)

    def list_connector_links(self, profile_id: str, household_id: str) -> list[ConnectorLink]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, connector, display_name, status, capabilities,
                       secret_provider, secret_reference, external_reference,
                       created_by_profile_id, last_synced_at, created_at, updated_at
                FROM connector_links
                WHERE household_id = %s
                ORDER BY updated_at DESC, id DESC
                """,
                (household_id,),
            ).fetchall()
        return [self._map_connector_link(row) for row in rows]

    def record_sync_run(
        self,
        *,
        profile_id: str,
        household_id: str,
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
            row = connection.execute(
                """
                INSERT INTO sync_runs (
                    id, household_id, connector_link_id, trigger, status, summary, stats,
                    created_by_profile_id, started_at, completed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id, household_id, connector_link_id, trigger, status, summary, stats,
                          created_by_profile_id, started_at, completed_at
                """,
                (
                    sync_id,
                    household_id,
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
            connection.commit()
        return self._map_sync_run(row)

    def list_sync_runs(self, profile_id: str, household_id: str, *, limit: int) -> list[SyncRun]:
        with get_connection() as connection:
            self._require_household_access(connection, profile_id, household_id)
            rows = connection.execute(
                """
                SELECT id, household_id, connector_link_id, trigger, status, summary, stats,
                       created_by_profile_id, started_at, completed_at
                FROM sync_runs
                WHERE household_id = %s
                ORDER BY started_at DESC, id DESC
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
            "calculation_version": "ai-ready-mvp-1",
            "latest_imported_at": row["latest_imported_at"],
            "latest_snapshot_at": row["latest_snapshot_at"],
            "latest_sync_completed_at": row["latest_sync_completed_at"],
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
    def _map_connector_link(row: dict) -> ConnectorLink:
        return ConnectorLink(
            id=row["id"],
            household_id=row["household_id"],
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
        )

    @staticmethod
    def _map_sync_run(row: dict) -> SyncRun:
        return SyncRun(
            id=row["id"],
            household_id=row["household_id"],
            connector_link_id=row["connector_link_id"],
            trigger=row["trigger"],
            status=row["status"],
            summary=row["summary"],
            stats=_coerce_json(row["stats"]),
            created_by_profile_id=row["created_by_profile_id"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
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
    "ImportBatch",
    "LedgerStore",
    "REQUIRED_COLUMNS",
    "SUPPORTED_TYPES",
    "Transaction",
    "normalize_csv_rows",
    "repository",
]
