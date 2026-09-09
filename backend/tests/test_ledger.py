from app.services.engines import holdings, migration_plan, tax_lots
from app.services.ledger import LedgerStore


CSV = """account_id,symbol,transaction_date,quantity,price,type,cost_basis,lot_id,external_id
taxable-1,VTI,2024-01-01,10,200,BUY,1800,lot-1,trade-1
taxable-1,BND,2025-01-01,0.5,70,BUY,35,lot-2,trade-2
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
    assert lots[1]["holding_period"] == "long_term"
    assert lots[1]["unrealized_gain"] == "200"
    assert migration_plan(transactions, __import__("datetime").date(2026, 1, 2))[0]["action"] == "sell_fractional_share"


def test_duplicate_event_in_a_new_file_is_not_reimported() -> None:
    store = LedgerStore()
    store.import_csv("household-1", "ibkr", CSV)
    batch = store.import_csv("household-1", "ibkr", CSV + "taxable-1,VTI,2026-01-01,1,220,BUY,220,lot-3,trade-3\n")
    assert batch.row_count == 1
