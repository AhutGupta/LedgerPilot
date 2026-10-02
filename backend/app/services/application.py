from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.domain.models import HouseholdPerson, PortfolioPolicy
from app.services.engines import (
    allocation_breakdown,
    cash_deployment_plan,
    holdings,
    portfolio_summary,
    quarterly_review_report,
    realized_gains_summary,
    rebalance_actions,
    simulate_sale,
    tax_lots,
)
from app.services.ledger import CAPABILITIES, repository
from app.services.plaid import PlaidClient, holdings_to_csv


class HouseholdApplicationService:
    def create_plaid_link_token(self, profile_id: str, household_id: str, household_person_id: str) -> dict:
        person = self._resolve_person_for_write(profile_id, household_id, household_person_id)
        link_token = PlaidClient().create_link_token(profile_id=profile_id, person_name=person.full_name)
        return {"link_token": link_token, "person": person}

    def import_plaid_holdings(
        self,
        profile_id: str,
        household_id: str,
        household_person_id: str,
        public_token: str,
    ) -> dict:
        person = self._resolve_person_for_write(profile_id, household_id, household_person_id)
        access_token, item_id, holdings_payload = PlaidClient().exchange_and_get_holdings(public_token)
        csv_content = holdings_to_csv(holdings_payload, item_id=item_id)
        if len(csv_content.splitlines()) <= 1:
            raise ValueError("Plaid returned no supported investment positions for this account.")
        imported = self.import_csv(profile_id, household_id, person.id, "plaid", csv_content)
        connector = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            household_person_id=person.id,
            connector="plaid",
            display_name=f"{person.full_name} Plaid investment connection",
            status="active",
            secret_provider="env",
            secret_reference=None,
            external_reference=item_id,
            capabilities=CAPABILITIES["plaid"],
        )
        repository.save_plaid_access_token(
            connector_link_id=connector.id,
            item_id=item_id,
            access_token=access_token,
        )
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="plaid.initial_holdings.completed",
            entity_type="connector_link",
            entity_id=connector.id,
            details={
                "connector": "plaid",
                "item_id": item_id,
                "person_id": person.id,
                "row_count": imported["batch"].row_count,
            },
        )
        return {**imported, "connector": connector, "item_id": item_id}

    def import_csv(self, profile_id: str, household_id: str, household_person_id: str | None, connector: str, content: str) -> dict:
        person = self._resolve_person_for_write(profile_id, household_id, household_person_id)
        batch, idempotent = repository.import_csv(profile_id, household_id, person.id, connector, content)
        connector_link = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            household_person_id=person.id,
            connector=connector,
            display_name=f"{person.full_name} {connector.upper()} import channel",
            status="active" if CAPABILITIES[connector]["incremental_sync"] else "import_only",
            secret_provider="env",
            secret_reference=None,
            external_reference=f"{household_id}:{person.id}:{connector}",
            capabilities=CAPABILITIES[connector],
        )
        sync_run = repository.record_sync_run(
            profile_id=profile_id,
            household_id=household_id,
            household_person_id=person.id,
            connector_link_id=connector_link.id,
            trigger="import",
            status="succeeded",
            summary=(
                f"Replayed existing encrypted import batch {batch.id} for {person.full_name}."
                if idempotent
                else f"Imported {batch.row_count} new canonical transaction rows for {person.full_name} from {connector}."
            ),
            stats={
                "batch_id": batch.id,
                "connector": connector,
                "content_hash": batch.content_hash,
                "idempotent": idempotent,
                "person_id": person.id,
                "person_name": person.full_name,
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
                "person_id": person.id,
                "person_name": person.full_name,
                "row_count": batch.row_count,
                "snapshot_id": snapshot.id,
                "sync_run_id": sync_run.id,
            },
        )
        return {
            "batch": batch,
            "connector": connector_link,
            "idempotent": idempotent,
            "person": person,
            "snapshot": snapshot,
            "sync_run": sync_run,
        }

    def build_dashboard(self, profile_id: str, household_id: str, *, person_id: str | None = None) -> dict:
        timestamp = datetime.now(timezone.utc)
        analysis_date = timestamp.date()
        transactions = repository.for_household(profile_id, household_id)
        metadata = repository.snapshot_metadata(profile_id, household_id)
        latest_policy = repository.get_latest_policy(profile_id, household_id)
        household_imports = repository.list_import_batches(profile_id, household_id)
        household_accounts = repository.list_accounts(profile_id, household_id)
        household_connectors = repository.list_connector_links(profile_id, household_id)
        people = repository.list_household_people(profile_id, household_id)
        selected_person = self._resolve_selected_person(people, person_id)
        household_recent_transactions = repository.list_transactions(profile_id, household_id, limit=25)
        selected_person_recent_transactions = (
            repository.list_transactions(profile_id, household_id, selected_person.id, limit=25)
            if selected_person is not None
            else []
        )

        household_analysis = self._analysis_payload(transactions, analysis_date, latest_policy)
        imports_by_person = self._group_by_person(household_imports)
        accounts_by_person = self._group_by_person(household_accounts)
        connectors_by_person = self._group_by_person(household_connectors)
        transactions_by_person = self._group_transactions_by_person(transactions)
        dashboard = {
            **household_analysis,
            "policy": self._policy_payload(latest_policy),
            "recent_memory": [self._memory_payload(item) for item in repository.list_memory_entries(profile_id, household_id, limit=10)],
            "imports": [self._import_payload(item) for item in household_imports],
            "accounts": [self._account_payload(item) for item in household_accounts],
            "recent_transactions": [self._transaction_payload(item) for item in household_recent_transactions],
            "connectors": [self._connector_payload(item) for item in household_connectors],
            "recent_syncs": [self._sync_payload(item) for item in repository.list_sync_runs(profile_id, household_id, limit=10)],
            "latest_snapshot_id": self._latest_snapshot_id(profile_id, household_id),
            "people": [self._person_payload(item) for item in people],
            "person_portfolios": [
                self._person_portfolio_payload(
                    person,
                    transactions_by_person.get(person.id, []),
                    imports_by_person.get(person.id, []),
                    accounts_by_person.get(person.id, []),
                    analysis_date,
                    latest_policy,
                )
                for person in people
            ],
            "selected_person": self._person_payload(selected_person) if selected_person else None,
            "selected_person_dashboard": self._selected_person_dashboard(
                selected_person,
                transactions_by_person,
                imports_by_person,
                accounts_by_person,
                connectors_by_person,
                selected_person_recent_transactions,
                analysis_date,
                latest_policy,
            ),
            **metadata,
            "warnings": self._warnings(metadata, latest_policy),
            "as_of": timestamp,
        }
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
                "recommendations": dashboard["recommendations"],
                "policy": dashboard["policy"],
                "warnings": dashboard["warnings"],
                "people": dashboard["people"],
                "person_portfolios": dashboard["person_portfolios"],
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

    def create_person(self, profile_id: str, household_id: str, *, full_name: str) -> dict:
        person = repository.create_household_person(profile_id, household_id, full_name)
        snapshot = self.capture_snapshot(profile_id, household_id, snapshot_type="dashboard")
        return {"person": self._person_payload(person), "snapshot": self._snapshot_payload(snapshot)}

    def list_people(self, profile_id: str, household_id: str) -> list[dict]:
        return [self._person_payload(item) for item in repository.list_household_people(profile_id, household_id)]

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
        household_person_id: str | None = None,
    ) -> dict:
        person = self._resolve_person_for_write(profile_id, household_id, household_person_id)
        link = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            household_person_id=person.id,
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
            details={"connector": connector, "status": status, "person_id": person.id, "person_name": person.full_name},
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
        household_person_id: str | None = None,
    ) -> dict:
        person = self._resolve_person_for_write(profile_id, household_id, household_person_id)
        connector_link = repository.upsert_connector_link(
            profile_id=profile_id,
            household_id=household_id,
            household_person_id=person.id,
            connector=connector,
            display_name=f"{person.full_name} {connector.upper()} connector",
            status="active" if CAPABILITIES[connector]["incremental_sync"] else "import_only",
            secret_provider="env",
            secret_reference=None,
            external_reference=f"{household_id}:{person.id}:{connector}",
            capabilities=CAPABILITIES[connector],
        )
        resolved_status = status or ("succeeded" if CAPABILITIES[connector]["incremental_sync"] else "skipped")
        resolved_summary = summary or (
            f"Connector {connector} refresh recorded for {person.full_name}."
            if CAPABILITIES[connector]["incremental_sync"]
            else f"Connector {connector} requires file import for {person.full_name} in this MVP."
        )
        sync_run = repository.record_sync_run(
            profile_id=profile_id,
            household_id=household_id,
            household_person_id=person.id,
            connector_link_id=connector_link.id,
            trigger=trigger,
            status=resolved_status,
            summary=resolved_summary,
            stats={"connector": connector, "person_id": person.id, "person_name": person.full_name},
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
            details={"connector": connector, "status": resolved_status, "person_id": person.id, "person_name": person.full_name, "snapshot_id": snapshot.id},
        )
        return {"snapshot": self._snapshot_payload(snapshot), "sync_run": self._sync_payload(sync_run)}

    def build_recommendations(self, profile_id: str, household_id: str) -> dict:
        dashboard = self.build_dashboard(profile_id, household_id)
        return {
            "policy": dashboard["policy"],
            "recommendations": dashboard["recommendations"],
            "cash_deployment": dashboard["cash_deployment"],
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

    def list_accounts(self, profile_id: str, household_id: str, *, person_id: str | None = None) -> list[dict]:
        return [self._account_payload(item) for item in repository.list_accounts(profile_id, household_id, person_id)]

    def list_transactions(self, profile_id: str, household_id: str, *, person_id: str | None = None, limit: int = 100) -> list[dict]:
        return [self._transaction_payload(item) for item in repository.list_transactions(profile_id, household_id, person_id, limit=limit)]

    def latest_policy(self, profile_id: str, household_id: str) -> dict | None:
        return self._policy_payload(repository.get_latest_policy(profile_id, household_id))

    @staticmethod
    def _analysis_payload(transactions: list, analysis_date: date, latest_policy: PortfolioPolicy | None) -> dict:
        summary = portfolio_summary(transactions, analysis_date)
        recommendations = rebalance_actions(transactions, latest_policy)
        return {
            "summary": summary,
            "holdings": holdings(transactions),
            "allocation": allocation_breakdown(transactions),
            "tax_lots": tax_lots(transactions, analysis_date),
            "realized_gains": realized_gains_summary(transactions, analysis_date),
            "recommendations": recommendations,
            "cash_deployment": cash_deployment_plan(
                transactions,
                latest_policy,
                Decimal(summary["cash_like_market_value"]),
            ),
        }

    @staticmethod
    def _group_by_person(items: list) -> dict[str, list]:
        grouped: dict[str, list] = defaultdict(list)
        for item in items:
            grouped[item.household_person_id].append(item)
        return grouped

    @staticmethod
    def _group_transactions_by_person(items: list) -> dict[str, list]:
        grouped: dict[str, list] = defaultdict(list)
        for item in items:
            grouped[item.household_person_id].append(item)
        return grouped

    @staticmethod
    def _resolve_selected_person(people: list[HouseholdPerson], person_id: str | None) -> HouseholdPerson | None:
        if not people:
            return None
        if person_id:
            for person in people:
                if person.id == person_id:
                    return person
            raise LookupError("person not found")
        return next((person for person in people if person.is_linked_profile), people[0])

    def _resolve_person_for_write(self, profile_id: str, household_id: str, person_id: str | None) -> HouseholdPerson:
        people = repository.list_household_people(profile_id, household_id)
        person = self._resolve_selected_person(people, person_id)
        if person is None:
            raise LookupError("person not found")
        return person

    def _selected_person_dashboard(
        self,
        selected_person: HouseholdPerson | None,
        transactions_by_person: dict[str, list],
        imports_by_person: dict[str, list],
        accounts_by_person: dict[str, list],
        connectors_by_person: dict[str, list],
        selected_person_recent_transactions: list,
        analysis_date: date,
        latest_policy: PortfolioPolicy | None,
    ) -> dict | None:
        if selected_person is None:
            return None
        transactions = transactions_by_person.get(selected_person.id, [])
        analysis = self._analysis_payload(transactions, analysis_date, latest_policy)
        return {
            **analysis,
            "imports": [self._import_payload(item) for item in imports_by_person.get(selected_person.id, [])],
            "accounts": [self._account_payload(item) for item in accounts_by_person.get(selected_person.id, [])],
            "connectors": [self._connector_payload(item) for item in connectors_by_person.get(selected_person.id, [])],
            "recent_transactions": [self._transaction_payload(item) for item in selected_person_recent_transactions],
        }

    def _person_portfolio_payload(
        self,
        person: HouseholdPerson,
        transactions: list,
        imports: list,
        accounts: list,
        analysis_date: date,
        latest_policy: PortfolioPolicy | None,
    ) -> dict:
        analysis = self._analysis_payload(transactions, analysis_date, latest_policy)
        latest_imported_at = max((item.imported_at for item in imports), default=None)
        return {
            "person": self._person_payload(person),
            "summary": analysis["summary"],
            "account_count": len(accounts),
            "import_count": len(imports),
            "latest_imported_at": latest_imported_at,
        }

    @staticmethod
    def _warnings(metadata: dict[str, Any], latest_policy: PortfolioPolicy | None) -> list[str]:
        warnings = []
        if metadata["freshness"] == "stale":
            warnings.append("Portfolio data is stale; analyses are derived from the latest stored snapshot and sync state.")
        if metadata["freshness"] == "unavailable":
            warnings.append("No imports or completed syncs are stored for this household yet.")
        if latest_policy is None:
            warnings.append("No household policy is stored yet; policy-driven recommendations are unavailable.")
        return warnings

    @staticmethod
    def _latest_snapshot_id(profile_id: str, household_id: str) -> str | None:
        snapshot = repository.get_latest_snapshot(profile_id, household_id)
        return snapshot.id if snapshot else None

    @staticmethod
    def _account_payload(item) -> dict:
        return asdict(item)

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
            "person_name": item.person_name,
            "household_person_id": item.household_person_id,
            "row_count": item.row_count,
        }

    @staticmethod
    def _memory_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _person_payload(item) -> dict | None:
        return None if item is None else asdict(item)

    @staticmethod
    def _policy_payload(item) -> dict | None:
        return None if item is None else asdict(item)

    @staticmethod
    def _snapshot_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _sync_payload(item) -> dict:
        return asdict(item)

    @staticmethod
    def _transaction_payload(item) -> dict:
        return asdict(item)


household_app_service = HouseholdApplicationService()
