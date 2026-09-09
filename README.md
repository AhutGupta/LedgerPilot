# LedgerPilot

LedgerPilot is a self-hosted, multi-user portfolio intelligence platform for households.
It is **read-only and advisory** (Observe → Analyze → Recommend → Explain), with no trade execution or money movement.

## Implemented vertical slice

The backend now provides a small, runnable read-only API for the initial workflow:

- `POST /api/v1/households/{household_id}/imports/{connector}` accepts a CSV body.
- `GET /api/v1/households/{household_id}/holdings`, `tax-lots`, and `migration-plan` return derived views with snapshot metadata.
- `GET /api/v1/connectors` exposes explicit capability flags for IBKR, Fidelity, Robinhood, BofA, and Wealthfront.

CSV requires `account_id,symbol,transaction_date,quantity,price,type`; optional
`market_price,cost_basis,lot_id,external_id` improve valuation, tax-lot reconstruction,
and idempotency.
Supported types are `BUY`, `SELL`, and `TRANSFER_IN`. Import batches retain raw input
and a content hash; external event IDs prevent duplicated transactions across imports.

Run locally:

```bash
cd backend
python -m pip install -e ".[dev]"
uvicorn app.main:app --reload
pytest
```

This is intentionally an in-memory first vertical slice. Replacing `LedgerStore` with
a PostgreSQL-backed repository, adding encrypted raw-object storage, and implementing
the IBKR read-only activity import are the next production steps.

### Run the web app with Docker

From the repository root, run `docker compose -f infra/docker-compose.yml up --build`.
Then visit <http://localhost:8000>. Choose a household and connector, upload a CSV, and
the same page displays the derived holdings with freshness metadata. Data is intentionally
in-memory in this milestone, so it resets when the container restarts.

The UI is a single same-origin FastAPI-served page for this feature. A Next.js read-only
application should replace it once the canonical repository and persisted snapshots are
in place; it must retain the visible `as_of`, source, freshness, and sync-status indicators.

## Product boundaries (MVP)

- ✅ Consolidate data from broker/bank connectors and file imports
- ✅ Normalize into a canonical ledger with provenance and auditability
- ✅ Run deterministic portfolio, tax, and migration analysis
- ✅ Expose results in a read-only UI and via an AI assistant tool API
- ❌ No buy/sell/transfer execution APIs

## Optimized data path

```text
Connectors
  -> Raw Import + Provenance
  -> Normalizer
  -> Canonical Ledger (authoritative)
  -> Materialized Portfolio Views (cached read models)
  -> FastAPI
  -> Read-only UI / AI Agent
```

Each materialized view carries: `as_of`, `source`, `freshness`, `sync_status`.

## Canonical domain skeleton

Core entities:

- Tenant, Household, Person, Account
- Security, Transaction, TaxLot
- Position, Snapshot, Recommendation
- ImportBatch, BrokerConnection
- Policy, MemoryEntry, AuditEvent

Rules:

- Transactions and imported lots are source of truth.
- Positions/summaries are derived and rebuildable.
- Memory and policy never override canonical financial facts.

## Materialized views & refresh skeleton

Read path defaults to local snapshots (`PortfolioSnapshot`, `AccountSnapshot`) with TTL-based caching.

Refresh triggers:

- scheduled sync
- manual refresh
- connector webhook (if available)
- forced refresh before freshness-sensitive analysis

Behavior:

- If stale, APIs return cached data **with explicit stale warning**.
- Broker outages do not break reads; last known portfolio remains available.

## Deterministic analysis engines

- **Portfolio**: allocation, drift, rebalance simulation, cash deployment
- **Tax**: realized/unrealized gains, holding period, wash-sale checks, disposal simulation
- **Migration**: in-kind vs sell, unsupported/fractional holdings, staged IBKR migration
- **Reports**: quarterly review, YTD realized gains estimate, migration plan

## AI integration contract

AI model (Ollama; Qwen3.5 4B / Phi-4-mini) acts as an API client only.

The assistant can call explicit tools like:

- `get_portfolio_summary`
- `simulate_sale`
- `generate_migration_plan`
- `generate_report`

It has no direct DB access and no execution endpoints.

## Security and audit skeleton

- Secrets accessed through pluggable `SecretProvider`
  - Windows Credential Manager, 1Password, Vault, env vars, AWS Secrets Manager
- Secrets never stored in config/portfolio tables.
- Append-only audit trail for imports, memory/policy changes, recommendations, recalculations, and user-reported actions.

## Suggested repository skeleton

```text
backend/
  app/
    api/
      routes/
        households.py
        accounts.py
        holdings.py
        transactions.py
        tax.py
        migration.py
        recommendations.py
        reports.py
        memory.py
        audit.py
    domain/
      models/            # canonical ledger models
      views/             # materialized read models
      policies/
      memory/
    services/
      connectors/        # Plaid, IBKR, CSV/QFX/OFX
      normalization/
      sync/
      engines/           # portfolio/tax/migration deterministic logic
      reporting/
      pricing/           # short-lived market cache
      provenance/
      audit/
      secrets/
    ai/
      tools/
      orchestration/
    db/
      migrations/        # Alembic
      session.py
    main.py              # FastAPI app

frontend/
  src/
    pages/
      households/
      accounts/
      holdings/
      tax/
      migration/
      recommendations/
      reports/
      memory/
      audit/

infra/
  docker-compose.yml
  env/
```

## Killer workflow (MVP)

1. Connect accounts
2. Reconstruct portfolio and tax lots
3. Define portfolio policy
4. Assess allocation + tax posture
5. Determine in-kind vs taxable moves
6. Generate staged IBKR consolidation plan
7. User executes externally
8. Next sync reconciles actual state
