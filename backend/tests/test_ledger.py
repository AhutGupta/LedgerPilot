import pytest

from app.services.engines import holdings, tax_lots
from app.services.ledger import LedgerStore, normalize_csv_rows


CSV = """account_id,symbol,transaction_date,quantity,price,market_price,type,cost_basis,lot_id,external_id
taxable-1,VTI,2024-01-01,10,200,200,BUY,1800,lot-1,trade-1
taxable-1,BND,2025-01-01,0.5,70,70,BUY,35,lot-2,trade-2
"""


def test_import_is_idempotent_and_derives_analysis() -> None:
    store = LedgerStore()
    batch = store.import_csv("household-1", "person-1", "ibkr", CSV)
    assert batch.row_count == 2
    assert store.import_csv("household-1", "person-1", "ibkr", CSV).id == batch.id

    transactions = store.for_household("household-1")
    assert holdings(transactions) == [
        {"account_id": "taxable-1", "symbol": "BND", "quantity": "0.5"},
        {"account_id": "taxable-1", "symbol": "VTI", "quantity": "10"},
    ]
    lots = tax_lots(transactions, as_of=__import__("datetime").date(2026, 1, 2))
    vti_lot = next(lot for lot in lots if lot["symbol"] == "VTI")
    assert vti_lot["holding_period"] == "long_term"
    assert vti_lot["unrealized_gain"] == "200"


def test_duplicate_event_in_a_new_file_is_not_reimported() -> None:
    store = LedgerStore()
    store.import_csv("household-1", "person-1", "ibkr", CSV)
    batch = store.import_csv(
        "household-1",
        "person-1",
        "ibkr",
        CSV + "taxable-1,VTI,2026-01-01,1,220,220,BUY,220,lot-3,trade-3\n",
    )
    assert batch.row_count == 1


def test_sale_reduces_the_oldest_open_tax_lot() -> None:
    store = LedgerStore()
    store.import_csv(
        "household-1",
        "person-1",
        "ibkr",
        """account_id,symbol,transaction_date,quantity,price,type,external_id
taxable-1,VTI,2024-01-01,10,100,BUY,buy-1
taxable-1,VTI,2025-01-01,4,120,SELL,sell-1
""",
    )
    lot = tax_lots(store.for_household("household-1"), __import__("datetime").date(2026, 1, 2))[0]
    assert lot["quantity"] == "6"
    assert lot["cost_basis"] == "600"


def test_person_scoped_imports_roll_up_to_household() -> None:
    store = LedgerStore()
    store.import_csv(
        "household-1",
        "person-a",
        "ibkr",
        """account_id,symbol,transaction_date,quantity,price,type,external_id
joint-1,VTI,2024-01-01,10,100,BUY,buy-a
""",
    )
    store.import_csv(
        "household-1",
        "person-b",
        "ibkr",
        """account_id,symbol,transaction_date,quantity,price,type,external_id
ira-1,BND,2024-01-02,5,80,BUY,buy-b
""",
    )

    assert [item.symbol for item in store.for_household("household-1", "person-a")] == ["VTI"]
    assert [item.symbol for item in store.for_household("household-1", "person-b")] == ["BND"]
    assert sorted(item.symbol for item in store.for_household("household-1")) == ["BND", "VTI"]


def test_normalize_csv_rows_accepts_broker_style_headers_and_amount_derived_price() -> None:
    rows = normalize_csv_rows(
        """Account Number,Security Symbol,Trade Date,Shares,Net Amount,Action,Current Price,Transaction ID
acct-9,VTI,01/15/2026,-2,410.00,Sell,205.00,trade-9
""",
        "household-1",
        "person-1",
        "batch-1",
    )

    assert rows[0].account_id == "acct-9"
    assert rows[0].symbol == "VTI"
    assert rows[0].transaction_type == "SELL"
    assert str(rows[0].quantity) == "2"
    assert str(rows[0].price) == "205.00"
    assert str(rows[0].market_price) == "205.00"


def test_normalize_csv_rows_reports_missing_headers_usefully() -> None:
    with pytest.raises(ValueError, match="Found headers: Account,Symbol"):
        normalize_csv_rows(
            """Account,Symbol
acct-1,VTI
""",
            "household-1",
            "person-1",
            "batch-1",
        )
