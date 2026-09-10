from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.models import PortfolioPolicy
from app.services.engines import (
    allocation_breakdown,
    cash_deployment_plan,
    holdings,
    migration_plan,
    portfolio_summary,
    quarterly_review_report,
    realized_gains_summary,
    rebalance_actions,
    simulate_sale,
    tax_lots,
)
from app.services.ledger import CAPABILITIES, repository


class HouseholdApplicationService:
    def import_csv(self, profile_id: str, household_id: str, connector: str, content: str) -> dict:
        batch, idempotent = repository.import_csv(profile_id, household_id, connector, content)
        connector_link = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            connector=connector,
            display_name=f"{connector.upper()} import channel",
            status="active" if CAPABILITIES[connector]["incremental_sync"] else "import_only",
            secret_provider="env",
            secret_reference=None,
            external_reference=f"{household_id}:{connector}",
            capabilities=CAPABILITIES[connector],
        )
        sync_run = repository.record_sync_run(
            profile_id=profile_id,
            household_id=household_id,
            connector_link_id=connector_link.id,
            trigger="import",
            status="succeeded",
            summary=(
                f"Replayed existing encrypted import batch {batch.id}."
                if idempotent
                else f"Imported {batch.row_count} new canonical transaction rows from {connector}."
            ),
            stats={
                "batch_id": batch.id,
                "connector": connector,
                "content_hash": batch.content_hash,
                "idempotent": idempotent,
                "row_count": batch.row_count,
            },
        )
        snapshot = self.capture_snapshot(
            profile_id,
            household_id,
            snapshot_type="dashboard",
            based_on_sync_run_id=sync_run.id,
        )
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="import.csv.completed",
            entity_type="import_batch",
            entity_id=batch.id,
            details={
                "connector": connector,
                "content_hash": batch.content_hash,
                "idempotent": idempotent,
                "row_count": batch.row_count,
                "snapshot_id": snapshot.id,
                "sync_run_id": sync_run.id,
            },
        )
        return {
            "batch": batch,
            "connector": connector_link,
            "idempotent": idempotent,
            "snapshot": snapshot,
            "sync_run": sync_run,
        }

    def build_dashboard(self, profile_id: str, household_id: str, *, as_of: date | None = None) -> dict:
        timestamp = datetime.now(timezone.utc)
        analysis_date = as_of or timestamp.date()
        transactions = repository.for_household(profile_id, household_id)
        metadata = repository.snapshot_metadata(profile_id, household_id)
        latest_policy = repository.get_latest_policy(profile_id, household_id)
        dashboard = {
            "summary": portfolio_summary(transactions, analysis_date),
            "holdings": holdings(transactions),
            "allocation": allocation_breakdown(transactions),
            "tax_lots": tax_lots(transactions, analysis_date),
            "realized_gains": realized_gains_summary(transactions, analysis_date),
            "migration_plan": migration_plan(transactions, analysis_date, policy=latest_policy),
            "recommendations": rebalance_actions(transactions, latest_policy),
            "policy": self._policy_payload(latest_policy),
            "recent_memory": [self._memory_payload(item) for item in repository.list_memory_entries(profile_id, household_id, limit=10)],
            "imports": [self._import_payload(item) for item in repository.list_import_batches(profile_id, household_id)],
            "connectors": [self._connector_payload(item) for item in repository.list_connector_links(profile_id, household_id)],
            "recent_syncs": [self._sync_payload(item) for item in repository.list_sync_runs(profile_id, household_id, limit=10)],
            "latest_snapshot_id": self._latest_snapshot_id(profile_id, household_id),
            **metadata,
            "warnings": self._warnings(metadata, latest_policy),
            "as_of": timestamp,
        }
        dashboard["cash_deployment"] = cash_deployment_plan(
            transactions,
            latest_policy,
            Decimal(dashboard["summary"]["cash_like_market_value"]),
        )
        return dashboard

    def capture_snapshot(
        self,
        profile_id: str,
        household_id: str,
        *,
        snapshot_type: str,
        based_on_sync_run_id: str | None = None,
    ):
        dashboard = self.build_dashboard(profile_id, household_id)
        snapshot = repository.create_snapshot(
            profile_id=profile_id,
            household_id=household_id,
            snapshot_type=snapshot_type,
            source=dashboard["source"],
            freshness=dashboard["freshness"],
            sync_status=dashboard["sync_status"],
            payload={
                "summary": dashboard["summary"],
                "allocation": dashboard["allocation"],
                "tax_lots": dashboard["tax_lots"],
                "realized_gains": dashboard["realized_gains"],
                "migration_plan": dashboard["migration_plan"],
                "recommendations": dashboard["recommendations"],
                "policy": dashboard["policy"],
                "warnings": dashboard["warnings"],
                "as_of": dashboard["as_of"],
            },
            based_on_sync_run_id=based_on_sync_run_id,
        )
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="snapshot.created",
            entity_type="snapshot",
            entity_id=snapshot.id,
            details={"snapshot_type": snapshot_type, "based_on_sync_run_id": based_on_sync_run_id},
        )
        return snapshot

    def save_policy(
        self,
        profile_id: str,
        household_id: str,
        *,
        name: str,
        target_allocations: dict[str, Any],
        rebalance_threshold_pct: Any,
        cash_reserve_target_pct: Any,
        max_single_position_pct: Any = None,
        notes: str | None = None,
    ) -> dict:
        policy = repository.save_policy(
            profile_id=profile_id,
            household_id=household_id,
            name=name,
            target_allocations=target_allocations,
            rebalance_threshold_pct=rebalance_threshold_pct,
            cash_reserve_target_pct=cash_reserve_target_pct,
            max_single_position_pct=max_single_position_pct,
            notes=notes,
        )
        snapshot = self.capture_snapshot(profile_id, household_id, snapshot_type="dashboard")
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="policy.saved",
            entity_type="policy",
            entity_id=policy.id,
            details={"policy_name": policy.name, "snapshot_id": snapshot.id},
        )
        return {"policy": self._policy_payload(policy), "snapshot": self._snapshot_payload(snapshot)}

    def add_memory_entry(
        self,
        profile_id: str,
        household_id: str,
        *,
        entry_type: str,
        content: str,
        labels: list[str] | None,
        importance: str,
    ) -> dict:
        entry = repository.add_memory_entry(
            profile_id=profile_id,
            household_id=household_id,
            entry_type=entry_type,
            content=content,
            labels=labels or [],
            importance=importance,
        )
        snapshot = self.capture_snapshot(profile_id, household_id, snapshot_type="dashboard")
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="memory.saved",
            entity_type="memory_entry",
            entity_id=entry.id,
            details={"entry_type": entry.entry_type, "snapshot_id": snapshot.id},
        )
        return {"entry": self._memory_payload(entry), "snapshot": self._snapshot_payload(snapshot)}

    def register_connector(
        self,
        profile_id: str,
        household_id: str,
        *,
        connector: str,
        display_name: str,
        status: str,
        secret_provider: str,
        secret_reference: str | None,
        external_reference: str | None,
    ) -> dict:
        link = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            connector=connector,
            display_name=display_name,
            status=status,
            secret_provider=secret_provider,
            secret_reference=secret_reference,
            external_reference=external_reference,
            capabilities=CAPABILITIES[connector],
        )
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="connector.saved",
            entity_type="connector_link",
            entity_id=link.id,
            details={"connector": connector, "status": status},
        )
        return {"connector": self._connector_payload(link)}

    def record_sync(
        self,
        profile_id: str,
        household_id: str,
        *,
        connector: str,
        trigger: str,
        status: str | None = None,
        summary: str | None = None,
    ) -> dict:
        connector_link = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            connector=connector,
            display_name=f"{connector.upper()} connector",
            status="active" if CAPABILITIES[connector]["incremental_sync"] else "import_only",
            secret_provider="env",
            secret_reference=None,
            external_reference=f"{household_id}:{connector}",
            capabilities=CAPABILITIES[connector],
        )
        resolved_status = status or ("succeeded" if CAPABILITIES[connector]["incremental_sync"] else "skipped")
        resolved_summary = summary or (
            f"Connector {connector} refresh recorded."
            if CAPABILITIES[connector]["incremental_sync"]
            else f"Connector {connector} requires file import for new data in this MVP."
        )
        sync_run = repository.record_sync_run(
            profile_id=profile_id,
            household_id=household_id,
            connector_link_id=connector_link.id,
            trigger=trigger,
            status=resolved_status,
            summary=resolved_summary,
            stats={"connector": connector},
        )
        snapshot = self.capture_snapshot(
            profile_id,
            household_id,
            snapshot_type="dashboard",
            based_on_sync_run_id=sync_run.id,
        )
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="sync.recorded",
            entity_type="sync_run",
            entity_id=sync_run.id,
            details={"connector": connector, "status": resolved_status, "snapshot_id": snapshot.id},
        )
        return {"snapshot": self._snapshot_payload(snapshot), "sync_run": self._sync_payload(sync_run)}

    def build_recommendations(self, profile_id: str, household_id: str) -> dict:
        dashboard = self.build_dashboard(profile_id, household_id)
        return {
            "policy": dashboard["policy"],
            "recommendations": dashboard["recommendations"],
            "cash_deployment": dashboard["cash_deployment"],
            "migration_plan": dashboard["migration_plan"],
            "warnings": dashboard["warnings"],
            "as_of": dashboard["as_of"],
        }

    def build_report(self, profile_id: str, household_id: str, *, report_type: str) -> dict:
        transactions = repository.for_household(profile_id, household_id)
        latest_policy = repository.get_latest_policy(profile_id, household_id)
        today = date.today()
        if report_type == "quarterly_review":
            return quarterly_review_report(transactions, today, latest_policy)
        if report_type == "ytd_realized_gains":
            realized = realized_gains_summary(transactions, today)
            return {
                "report_type": "ytd_realized_gains",
                "as_of": today.isoformat(),
                **realized,
            }
        raise ValueError(f"unsupported report type: {report_type}")

    def simulate_sale(self, profile_id: str, household_id: str, *, symbol: str, quantity: Any, sale_price: Any, account_id: str | None) -> dict:
        transactions = repository.for_household(profile_id, household_id)
        try:
            sale_quantity = Decimal(str(quantity))
            price = Decimal(str(sale_price))
        except InvalidOperation as exc:
            raise ValueError("quantity and sale_price must be valid decimal values") from exc
        return simulate_sale(
            transactions,
            as_of=date.today(),
            symbol=symbol,
            quantity=sale_quantity,
            sale_price=price,
            account_id=account_id,
        )

    def contract_metadata(self, profile_id: str, household_id: str) -> dict:
        return repository.snapshot_metadata(profile_id, household_id)

    def list_memory(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._memory_payload(item) for item in repository.list_memory_entries(profile_id, household_id, limit=50)]

    def list_audit(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._audit_payload(item) for item in repository.list_audit_events(profile_id, household_id, limit=100)]

    def list_policies(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._policy_payload(item) for item in repository.list_policies(profile_id, household_id, limit=20)]

    def list_connectors(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._connector_payload(item) for item in repository.list_connector_links(profile_id, household_id)]

    def list_syncs(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._sync_payload(item) for item in repository.list_sync_runs(profile_id, household_id, limit=50)]

    def list_snapshots(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._snapshot_payload(item) for item in repository.list_snapshots(profile_id, household_id, limit=20)]

    def latest_policy(self, profile_id: str, household_id: str) -> dict | None:
        return self._policy_payload(repository.get_latest_policy(profile_id, household_id))

    @staticmethod
    def _warnings(metadata: dict[str, Any], latest_policy: PortfolioPolicy | None) -> list[str]:
        warnings = []
        if metadata["freshness"] == "stale":
            warnings.append("Portfolio data is stale; analyses are derived from the latest stored snapshot and sync state.")
        if metadata["freshness"] == "unavailable":
            warnings.append("No imports or completed syncs are stored for this household yet.")
        if latest_policy is None:
            warnings.append("No household policy is stored yet; recommendations are limited to migration heuristics.")
        return warnings

    @staticmethod
    def _latest_snapshot_id(profile_id: str, household_id: str) -> str | None:
        snapshot = repository.get_latest_snapshot(profile_id, household_id)
        return snapshot.id if snapshot else None

    @staticmethod
    def _audit_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _connector_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _import_payload(item) -> dict:
        return {
            "content_hash": item.content_hash,
            "connector": item.connector,
            "id": item.id,
            "imported_at": item.imported_at,
            "row_count": item.row_count,
        }

    @staticmethod
    def _memory_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _policy_payload(item) -> dict | None:
        return None if item is None else asdict(item)

    @staticmethod
    def _snapshot_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _sync_payload(item) -> dict:
        return asdict(item)


household_app_service = HouseholdApplicationService()
