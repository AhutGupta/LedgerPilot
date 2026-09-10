from app.services.engines import holdings, tax_lots
from app.services.ledger import LedgerStore


CSV = """account_id,symbol,transaction_date,quantity,price,market_price,type,cost_basis,lot_id,external_id
taxable-1,VTI,2024-01-01,10,200,200,BUY,1800,lot-1,trade-1
taxable-1,BND,2025-01-01,0.5,70,70,BUY,35,lot-2,trade-2
"""


def test_import_is_idempotent_and_derives_analysis() -> None:
    store = LedgerStore()
    batch = store.import_csv("household-1", "ibkr", CSV)
    assert batch.row_count == 2
    assert store.import_csv("household-1", "ibkr", CSV).id == batch.id

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
    store.import_csv("household-1", "ibkr", CSV)
    batch = store.import_csv("household-1", "ibkr", CSV + "taxable-1,VTI,2026-01-01,1,220,220,BUY,220,lot-3,trade-3\n")
    assert batch.row_count == 1


def test_sale_reduces_the_oldest_open_tax_lot() -> None:
    store = LedgerStore()
    store.import_csv("household-1", "ibkr", """account_id,symbol,transaction_date,quantity,price,type,external_id
taxable-1,VTI,2024-01-01,10,100,BUY,buy-1
taxable-1,VTI,2025-01-01,4,120,SELL,sell-1
""")
    lot = tax_lots(store.for_household("household-1"), __import__("datetime").date(2026, 1, 2))[0]
    assert lot["quantity"] == "6"
    assert lot["cost_basis"] == "600"
