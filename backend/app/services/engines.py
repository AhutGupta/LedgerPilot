"""Pure deterministic portfolio, tax, and reporting calculations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.domain.models import PortfolioPolicy, Transaction


CASH_LIKE_SYMBOLS = {"USD", "CASH", "SWVXX", "VMFXX"}
ZERO = Decimal("0")
HUNDRED = Decimal("100")


def tax_lots(transactions: list[Transaction], as_of: date) -> list[dict]:
    result = []
    for lot, quantity in _remaining_lots(transactions):
        market_price = lot.market_price or lot.price
        cost_basis = lot.cost_basis * quantity / lot.quantity
        market_value = quantity * market_price
        gain = market_value - cost_basis
        result.append(
            {
                "account_id": lot.account_id,
                "symbol": lot.symbol,
                "lot_id": lot.lot_id,
                "quantity": _format_decimal(quantity),
                "cost_basis": _format_decimal(cost_basis),
                "market_value": _format_decimal(market_value),
                "unrealized_gain": _format_decimal(gain),
                "holding_period": "long_term" if (as_of - lot.transaction_date).days > 365 else "short_term",
            }
        )
    return result


def holdings(transactions: list[Transaction]) -> list[dict]:
    positions: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item in transactions:
        direction = Decimal("-1") if item.transaction_type == "SELL" else Decimal("1")
        positions[(item.account_id, item.symbol)] += direction * item.quantity
    return [
        {"account_id": account_id, "symbol": symbol, "quantity": _format_decimal(quantity)}
        for (account_id, symbol), quantity in sorted(positions.items())
        if quantity
    ]


def allocation_breakdown(transactions: list[Transaction]) -> list[dict]:
    symbol_values: dict[str, Decimal] = defaultdict(Decimal)
    total_market_value = ZERO
    for lot, quantity in _remaining_lots(transactions):
        market_value = quantity * (lot.market_price or lot.price)
        symbol_values[lot.symbol] += market_value
        total_market_value += market_value
    breakdown = []
    for symbol, market_value in sorted(symbol_values.items()):
        weight_pct = ZERO if not total_market_value else (market_value / total_market_value) * HUNDRED
        breakdown.append(
            {
                "symbol": symbol,
                "market_value": _format_decimal(market_value),
                "weight_pct": _format_decimal(weight_pct),
                "asset_class": "cash_like" if symbol in CASH_LIKE_SYMBOLS else "security",
            }
        )
    return breakdown


def portfolio_summary(transactions: list[Transaction], as_of: date) -> dict:
    open_lots = tax_lots(transactions, as_of)
    total_market_value = sum((Decimal(item["market_value"]) for item in open_lots), ZERO)
    total_cost_basis = sum((Decimal(item["cost_basis"]) for item in open_lots), ZERO)
    cash_like_market_value = sum(
        (Decimal(item["market_value"]) for item in open_lots if item["symbol"] in CASH_LIKE_SYMBOLS),
        ZERO,
    )
    total_unrealized_gain = total_market_value - total_cost_basis
    positions = holdings(transactions)
    return {
        "accounts": len({transaction.account_id for transaction in transactions}),
        "positions": len(positions),
        "open_lots": len(open_lots),
        "total_market_value": _format_decimal(total_market_value),
        "total_cost_basis": _format_decimal(total_cost_basis),
        "total_unrealized_gain": _format_decimal(total_unrealized_gain),
        "cash_like_market_value": _format_decimal(cash_like_market_value),
        "cash_like_weight_pct": _format_decimal(
            ZERO if not total_market_value else (cash_like_market_value / total_market_value) * HUNDRED
        ),
    }


def realized_gains_summary(transactions: list[Transaction], as_of: date) -> dict:
    sales = []
    total_proceeds = ZERO
    total_cost_basis = ZERO
    short_term_gain = ZERO
    long_term_gain = ZERO
    for sale in _matched_sales(transactions, as_of):
        sales.append(sale)
        realized_gain = Decimal(sale["realized_gain"])
        total_proceeds += Decimal(sale["proceeds"])
        total_cost_basis += Decimal(sale["cost_basis"])
        if sale["holding_period"] == "long_term":
            long_term_gain += realized_gain
        else:
            short_term_gain += realized_gain
    return {
        "sales": sales,
        "total_proceeds": _format_decimal(total_proceeds),
        "total_cost_basis": _format_decimal(total_cost_basis),
        "total_realized_gain": _format_decimal(short_term_gain + long_term_gain),
        "short_term_realized_gain": _format_decimal(short_term_gain),
        "long_term_realized_gain": _format_decimal(long_term_gain),
    }


def rebalance_actions(transactions: list[Transaction], policy: PortfolioPolicy | None) -> list[dict]:
    targets = _policy_targets(policy)
    if not targets:
        return []
    allocation = allocation_breakdown(transactions)
    current = {item["symbol"]: Decimal(item["weight_pct"]) for item in allocation}
    market_values = {item["symbol"]: Decimal(item["market_value"]) for item in allocation}
    total_market_value = sum(market_values.values(), ZERO)
    threshold = Decimal(policy.rebalance_threshold_pct)
    actions = []
    for symbol in sorted(set(current) | set(targets)):
        if symbol in CASH_LIKE_SYMBOLS:
            continue
        current_pct = current.get(symbol, ZERO)
        target_pct = targets.get(symbol, ZERO)
        drift_pct = current_pct - target_pct
        if abs(drift_pct) < threshold:
            continue
        target_value = total_market_value * target_pct / HUNDRED
        current_value = market_values.get(symbol, ZERO)
        trade_value = abs(target_value - current_value)
        actions.append(
            {
                "symbol": symbol,
                "current_weight_pct": _format_decimal(current_pct),
                "target_weight_pct": _format_decimal(target_pct),
                "drift_pct": _format_decimal(drift_pct),
                "action": "trim" if drift_pct > 0 else "add",
                "estimated_trade_value": _format_decimal(trade_value),
                "policy_breach": bool(
                    policy.max_single_position_pct
                    and current_pct > Decimal(policy.max_single_position_pct)
                ),
            }
        )
    return actions


def cash_deployment_plan(
    transactions: list[Transaction],
    policy: PortfolioPolicy | None,
    additional_cash: Decimal,
) -> list[dict]:
    targets = _policy_targets(policy)
    if additional_cash <= ZERO or not targets:
        return []
    allocation = allocation_breakdown(transactions)
    current_weights = {item["symbol"]: Decimal(item["weight_pct"]) for item in allocation}
    current_values = {item["symbol"]: Decimal(item["market_value"]) for item in allocation}
    total_market_value = sum(current_values.values(), ZERO)
    reserve_pct = Decimal(policy.cash_reserve_target_pct) if policy else ZERO
    reserve_value = total_market_value * reserve_pct / HUNDRED
    deployable_cash = max(additional_cash - reserve_value, ZERO)
    if deployable_cash <= ZERO:
        return []
    total_after = total_market_value + deployable_cash
    recommendations = []
    for symbol, target_pct in sorted(targets.items()):
        desired_value = total_after * target_pct / HUNDRED
        gap = max(desired_value - current_values.get(symbol, ZERO), ZERO)
        if not gap:
            continue
        recommendations.append({"symbol": symbol, "gap": gap})
    total_gap = sum((item["gap"] for item in recommendations), ZERO)
    if not total_gap:
        return []
    return [
        {
            "symbol": item["symbol"],
            "target_weight_pct": _format_decimal(targets[item["symbol"]]),
            "current_weight_pct": _format_decimal(current_weights.get(item["symbol"], ZERO)),
            "recommended_cash": _format_decimal(deployable_cash * item["gap"] / total_gap),
        }
        for item in recommendations
    ]


def simulate_sale(
    transactions: list[Transaction],
    *,
    as_of: date,
    symbol: str,
    quantity: Decimal,
    sale_price: Decimal,
    account_id: str | None = None,
) -> dict:
    symbol = symbol.strip().upper()
    if quantity <= ZERO:
        raise ValueError("quantity must be positive")
    if sale_price < ZERO:
        raise ValueError("sale_price cannot be negative")
    remaining_quantity = quantity
    consumed_lots = []
    available_quantity = ZERO
    for lot, lot_quantity in _remaining_lots(transactions):
        if lot.symbol != symbol:
            continue
        if account_id and lot.account_id != account_id:
            continue
        available_quantity += lot_quantity
        if remaining_quantity <= ZERO:
            continue
        consumed = min(lot_quantity, remaining_quantity)
        if not consumed:
            continue
        cost_basis = lot.cost_basis * consumed / lot.quantity
        realized_gain = consumed * sale_price - cost_basis
        holding_period = "long_term" if (as_of - lot.transaction_date).days > 365 else "short_term"
        consumed_lots.append(
            {
                "account_id": lot.account_id,
                "lot_id": lot.lot_id,
                "quantity": _format_decimal(consumed),
                "cost_basis": _format_decimal(cost_basis),
                "proceeds": _format_decimal(consumed * sale_price),
                "realized_gain": _format_decimal(realized_gain),
                "holding_period": holding_period,
            }
        )
        remaining_quantity -= consumed
    if available_quantity < quantity:
        raise ValueError("insufficient quantity for requested sale simulation")
    total_cost_basis = sum((Decimal(item["cost_basis"]) for item in consumed_lots), ZERO)
    proceeds = quantity * sale_price
    realized_gain = proceeds - total_cost_basis
    post_sale_quantity = available_quantity - quantity
    return {
        "symbol": symbol,
        "account_id": account_id,
        "requested_quantity": _format_decimal(quantity),
        "sale_price": _format_decimal(sale_price),
        "available_quantity": _format_decimal(available_quantity),
        "post_sale_quantity": _format_decimal(post_sale_quantity),
        "proceeds": _format_decimal(proceeds),
        "cost_basis": _format_decimal(total_cost_basis),
        "realized_gain": _format_decimal(realized_gain),
        "lots": consumed_lots,
    }


def quarterly_review_report(transactions: list[Transaction], as_of: date, policy: PortfolioPolicy | None) -> dict:
    summary = portfolio_summary(transactions, as_of)
    realized = realized_gains_summary(transactions, as_of)
    recommendations = rebalance_actions(transactions, policy)
    top_concentration = next(
        (
            item
            for item in sorted(
                allocation_breakdown(transactions),
                key=lambda entry: Decimal(entry["weight_pct"]),
                reverse=True,
            )
            if item["asset_class"] != "cash_like"
        ),
        None,
    )
    risks = []
    if top_concentration:
        risks.append(
            f"Largest non-cash exposure is {top_concentration['symbol']} at {top_concentration['weight_pct']}% of market value."
        )
    if policy is None:
        risks.append("No household policy is stored, so drift controls and concentration checks are advisory only.")
    return {
        "report_type": "quarterly_review",
        "as_of": as_of.isoformat(),
        "summary": summary,
        "realized_gains": realized,
        "recommendations": recommendations[:5],
        "risks": risks,
    }


def _remaining_lots(transactions: list[Transaction]) -> list[tuple[Transaction, Decimal]]:
    remaining: list[list[Transaction | Decimal]] = []
    for transaction in sorted(transactions, key=lambda item: (item.transaction_date, item.id)):
        if transaction.transaction_type in {"BUY", "TRANSFER_IN"}:
            remaining.append([transaction, transaction.quantity])
            continue
        quantity_to_match = transaction.quantity
        for item in remaining:
            lot = item[0]
            quantity = item[1]
            if not isinstance(lot, Transaction):
                continue
            if (lot.account_id, lot.symbol) != (transaction.account_id, transaction.symbol):
                continue
            consumed = min(quantity, quantity_to_match)
            item[1] = quantity - consumed
            quantity_to_match -= consumed
            if not quantity_to_match:
                break
    return [
        (lot, quantity)
        for lot, quantity in ((item[0], item[1]) for item in remaining)
        if isinstance(lot, Transaction) and isinstance(quantity, Decimal) and quantity > ZERO
    ]


def _matched_sales(transactions: list[Transaction], as_of: date) -> list[dict]:
    remaining: list[list[Transaction | Decimal]] = []
    sales = []
    for transaction in sorted(transactions, key=lambda item: (item.transaction_date, item.id)):
        if transaction.transaction_type in {"BUY", "TRANSFER_IN"}:
            remaining.append([transaction, transaction.quantity])
            continue
        quantity_to_match = transaction.quantity
        sale_cost_basis = ZERO
        holding_period = "short_term"
        for item in remaining:
            lot = item[0]
            quantity = item[1]
            if not isinstance(lot, Transaction):
                continue
            if (lot.account_id, lot.symbol) != (transaction.account_id, transaction.symbol):
                continue
            consumed = min(quantity, quantity_to_match)
            if not consumed:
                continue
            lot_cost_basis = lot.cost_basis * consumed / lot.quantity
            sale_cost_basis += lot_cost_basis
            holding_period = "long_term" if (transaction.transaction_date - lot.transaction_date).days > 365 else "short_term"
            item[1] = quantity - consumed
            quantity_to_match -= consumed
            if not quantity_to_match:
                break
        sold_quantity = transaction.quantity - quantity_to_match
        proceeds = sold_quantity * (transaction.market_price or transaction.price)
        sales.append(
            {
                "account_id": transaction.account_id,
                "symbol": transaction.symbol,
                "quantity": _format_decimal(sold_quantity),
                "proceeds": _format_decimal(proceeds),
                "cost_basis": _format_decimal(sale_cost_basis),
                "realized_gain": _format_decimal(proceeds - sale_cost_basis),
                "holding_period": holding_period,
                "sale_date": transaction.transaction_date.isoformat(),
            }
        )
    return sales


def _policy_targets(policy: PortfolioPolicy | None) -> dict[str, Decimal]:
    if policy is None:
        return {}
    return {symbol: Decimal(value) for symbol, value in policy.target_allocations.items()}


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"
