"""Pure deterministic portfolio and migration calculations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.services.ledger import Transaction


def tax_lots(transactions: list[Transaction], as_of: date) -> list[dict]:
    remaining: list[tuple[Transaction, Decimal]] = []
    for transaction in sorted(transactions, key=lambda item: (item.transaction_date, item.id)):
        if transaction.transaction_type in {"BUY", "TRANSFER_IN"}:
            remaining.append((transaction, transaction.quantity))
            continue
        quantity_to_match = transaction.quantity
        for index, (lot, quantity) in enumerate(remaining):
            if (lot.account_id, lot.symbol) != (transaction.account_id, transaction.symbol):
                continue
            consumed = min(quantity, quantity_to_match)
            remaining[index] = (lot, quantity - consumed)
            quantity_to_match -= consumed
            if not quantity_to_match:
                break
    result = []
    for lot, quantity in remaining:
        if not quantity:
            continue
        market_price = lot.market_price or lot.price
        cost_basis = lot.cost_basis * quantity / lot.quantity
        market_value = quantity * market_price
        gain = market_value - cost_basis
        result.append({
            "account_id": lot.account_id, "symbol": lot.symbol, "lot_id": lot.lot_id,
            "quantity": str(quantity), "cost_basis": str(cost_basis),
            "market_value": str(market_value), "unrealized_gain": str(gain),
            "holding_period": "long_term" if (as_of - lot.transaction_date).days > 365 else "short_term",
        })
    return result


def holdings(transactions: list[Transaction]) -> list[dict]:
    positions: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item in transactions:
        direction = Decimal("-1") if item.transaction_type == "SELL" else Decimal("1")
        positions[(item.account_id, item.symbol)] += direction * item.quantity
    return [{"account_id": account_id, "symbol": symbol, "quantity": str(quantity)}
            for (account_id, symbol), quantity in sorted(positions.items()) if quantity]


def migration_plan(transactions: list[Transaction], as_of: date) -> list[dict]:
    plan = []
    for lot in tax_lots(transactions, as_of):
        quantity = Decimal(lot["quantity"])
        plan.append({
            **lot,
            "action": "transfer_in_kind" if quantity == quantity.to_integral_value() else "sell_fractional_share",
            "reason": "whole shares are eligible for in-kind review" if quantity == quantity.to_integral_value()
                      else "fractional shares generally require liquidation before transfer",
        })
    return plan
