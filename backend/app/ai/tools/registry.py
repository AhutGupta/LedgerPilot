from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from app.services.application import household_app_service
from app.services.ledger import repository


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class AIToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolDefinition, Callable[[str, str, dict[str, Any]], Any]]] = {
            "get_dashboard": (
                ToolDefinition(
                    name="get_dashboard",
                    description="Return the household dashboard read model, derived analyses, policy, memory, and sync metadata.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                lambda profile_id, household_id, _: household_app_service.build_dashboard(profile_id, household_id),
            ),
            "get_portfolio_summary": (
                ToolDefinition(
                    name="get_portfolio_summary",
                    description="Return allocation, holdings, realized gains, and policy-aware recommendations without exposing database access.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                lambda profile_id, household_id, _: {
                    key: value
                    for key, value in household_app_service.build_dashboard(profile_id, household_id).items()
                    if key in {"allocation", "holdings", "realized_gains", "recommendations", "summary", "warnings"}
                },
            ),
            "simulate_sale": (
                ToolDefinition(
                    name="simulate_sale",
                    description="Run a deterministic FIFO sale simulation for a tenant-authorized household position.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "account_id": {"type": ["string", "null"]},
                            "quantity": {"type": "string"},
                            "sale_price": {"type": "string"},
                            "symbol": {"type": "string"},
                        },
                        "required": ["symbol", "quantity", "sale_price"],
                        "additionalProperties": False,
                    },
                ),
                lambda profile_id, household_id, arguments: household_app_service.simulate_sale(
                    profile_id,
                    household_id,
                    symbol=arguments["symbol"],
                    quantity=arguments["quantity"],
                    sale_price=arguments["sale_price"],
                    account_id=arguments.get("account_id"),
                ),
            ),
            "generate_report": (
                ToolDefinition(
                    name="generate_report",
                    description="Generate a deterministic quarterly review or YTD realized gains report.",
                    input_schema={
                        "type": "object",
                        "properties": {
                            "report_type": {
                                "type": "string",
                                "enum": ["quarterly_review", "ytd_realized_gains"],
                            }
                        },
                        "required": ["report_type"],
                        "additionalProperties": False,
                    },
                ),
                lambda profile_id, household_id, arguments: household_app_service.build_report(
                    profile_id,
                    household_id,
                    report_type=arguments["report_type"],
                ),
            ),
            "list_memory": (
                ToolDefinition(
                    name="list_memory",
                    description="List household memory entries recorded for policy, preferences, and reconciliation context.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                lambda profile_id, household_id, _: household_app_service.list_memory(profile_id, household_id),
            ),
            "get_policy": (
                ToolDefinition(
                    name="get_policy",
                    description="Return the latest persisted household policy used by deterministic recommendation engines.",
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                ),
                lambda profile_id, household_id, _: household_app_service.latest_policy(profile_id, household_id),
            ),
        }

    def contract(self, profile_id: str, household_id: str) -> dict:
        metadata = household_app_service.contract_metadata(profile_id, household_id)
        return {
            "authorization": "bearer_token_plus_household_membership",
            "data_access": "service_only_no_direct_database_access",
            "household_id": household_id,
            "metadata": metadata,
            "tools": [
                {
                    "name": definition.name,
                    "description": definition.description,
                    "input_schema": definition.input_schema,
                }
                for definition, _ in self._tools.values()
            ],
        }

    def execute(self, profile_id: str, household_id: str, tool_name: str, arguments: dict[str, Any]) -> dict:
        try:
            definition, handler = self._tools[tool_name]
        except KeyError as exc:
            raise ValueError(f"unsupported AI tool: {tool_name}") from exc
        result = handler(profile_id, household_id, arguments)
        repository.record_audit_event(
            profile_id=profile_id,
            household_id=household_id,
            event_type="ai.tool.executed",
            entity_type="ai_tool",
            entity_id=definition.name,
            details={"arguments": arguments, "tool": definition.name},
        )
        return {
            "tool": definition.name,
            "household_id": household_id,
            "executed_at": datetime.now(timezone.utc),
            "result": result,
        }


ai_tool_registry = AIToolRegistry()
