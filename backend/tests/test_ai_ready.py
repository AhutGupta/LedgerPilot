from datetime import date, datetime, timezone
from decimal import Decimal

from app.domain.models import PortfolioPolicy, Transaction
from app.services.engines import quarterly_review_report, rebalance_actions, simulate_sale
from app.services.secrets.crypto import EncryptedPayloadCodec
from app.services.secrets.provider import SecretProviderRegistry, StaticSecretProvider


def _tx(*, tx_id: str, account_id: str, symbol: str, tx_date: str, quantity: str, price: str, tx_type: str) -> Transaction:
    return Transaction(
        id=tx_id,
        household_id="household-1",
        account_id=account_id,
        symbol=symbol,
        transaction_date=date.fromisoformat(tx_date),
        quantity=Decimal(quantity),
        price=Decimal(price),
        market_price=Decimal(price),
        transaction_type=tx_type,
        cost_basis=Decimal(quantity) * Decimal(price),
        lot_id=tx_id,
        source_id=tx_id,
        batch_id="batch-1",
    )


def test_encrypted_payload_codec_round_trips_without_plaintext_leakage() -> None:
    codec = EncryptedPayloadCodec(
        secret_provider_name="static",
        secret_reference="unit-test-key",
        provider_registry=SecretProviderRegistry([StaticSecretProvider({"unit-test-key": "super-secret"})]),
        fallback_secret="fallback",
    )
    payload = codec.encrypt_text("account_id,symbol\nacc-1,VTI\n", aad={"household_id": "household-1"})
    assert "VTI" not in payload
    assert codec.decrypt_text(payload) == "account_id,symbol\nacc-1,VTI\n"


def test_simulate_sale_uses_fifo_cost_basis() -> None:
    transactions = [
        _tx(tx_id="buy-1", account_id="taxable-1", symbol="VTI", tx_date="2024-01-01", quantity="10", price="100", tx_type="BUY"),
        _tx(tx_id="buy-2", account_id="taxable-1", symbol="VTI", tx_date="2024-06-01", quantity="5", price="120", tx_type="BUY"),
    ]
    result = simulate_sale(
        transactions,
        as_of=date(2026, 1, 2),
        symbol="VTI",
        quantity=Decimal("12"),
        sale_price=Decimal("150"),
        account_id="taxable-1",
    )
    assert result["cost_basis"] == "1240"
    assert result["realized_gain"] == "560"
    assert result["post_sale_quantity"] == "3"
    assert [lot["quantity"] for lot in result["lots"]] == ["10", "2"]


def test_rebalance_actions_and_quarterly_report_use_latest_policy() -> None:
    transactions = [
        _tx(tx_id="buy-1", account_id="taxable-1", symbol="VTI", tx_date="2024-01-01", quantity="10", price="200", tx_type="BUY"),
        _tx(tx_id="buy-2", account_id="taxable-1", symbol="BND", tx_date="2024-01-01", quantity="5", price="100", tx_type="BUY"),
    ]
    policy = PortfolioPolicy(
        id="policy-1",
        household_id="household-1",
        name="Core policy",
        target_allocations={"VTI": "50", "BND": "50"},
        rebalance_threshold_pct="5",
        cash_reserve_target_pct="0",
        max_single_position_pct="60",
        notes=None,
        created_by_profile_id="profile-1",
        created_at=datetime.now(timezone.utc),
    )
    actions = rebalance_actions(transactions, policy)
    assert actions[0]["symbol"] == "BND"
    assert actions[0]["action"] == "add"
    report = quarterly_review_report(transactions, date(2026, 1, 2), policy)
    assert report["report_type"] == "quarterly_review"
    assert report["recommendations"]
