from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class ConnectorLink:
    id: str
    household_id: str
    household_person_id: str
    connector: str
    display_name: str
    status: str
    capabilities: dict[str, Any]
    secret_provider: str
    secret_reference: str | None
    external_reference: str | None
    created_by_profile_id: str
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime
    person_name: str | None = None


@dataclass(frozen=True)
class SyncRun:
    id: str
    household_id: str
    household_person_id: str | None
    connector_link_id: str | None
    trigger: str
    status: str
    summary: str
    stats: dict[str, Any]
    created_by_profile_id: str
    started_at: datetime
    completed_at: datetime | None
    person_name: str | None = None


@dataclass(frozen=True)
class PortfolioPolicy:
    id: str
    household_id: str
    name: str
    target_allocations: dict[str, str]
    rebalance_threshold_pct: str
    cash_reserve_target_pct: str
    max_single_position_pct: str | None
    notes: str | None
    created_by_profile_id: str
    created_at: datetime


@dataclass(frozen=True)
class MemoryEntry:
    id: str
    household_id: str
    entry_type: str
    content: str
    labels: list[str]
    importance: str
    created_by_profile_id: str
    created_at: datetime


@dataclass(frozen=True)
class AuditEvent:
    id: str
    household_id: str
    event_type: str
    entity_type: str
    entity_id: str
    details: dict[str, Any]
    created_by_profile_id: str
    created_at: datetime


@dataclass(frozen=True)
class PortfolioSnapshot:
    id: str
    household_id: str
    snapshot_type: str
    source: str
    freshness: str
    sync_status: str
    payload: dict[str, Any]
    based_on_sync_run_id: str | None
    created_by_profile_id: str
    created_at: datetime
