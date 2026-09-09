# LedgerPilot

LedgerPilot is a self-hosted, multi-user portfolio intelligence platform for households.
It is **read-only and advisory** (Observe → Analyze → Recommend → Explain), with no trade execution or money movement.

## Implemented vertical slice

The MVP now spans a PostgreSQL-backed FastAPI API and a Next.js dashboard:

- `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, and `GET /api/v1/me` provide profile auth with signed bearer tokens.
- `GET /api/v1/households` and `POST /api/v1/households` expose household-scoped access under explicit memberships.
- `POST /api/v1/households/{household_id}/imports/{connector}` accepts a CSV body, stores the raw upload in a private backend directory, and persists normalized ledger rows into PostgreSQL.
- `GET /api/v1/households/{household_id}/dashboard` returns holdings, tax lots, migration plan, and snapshot metadata in one tenant-scoped payload.
- `GET /api/v1/households/{household_id}/holdings`, `tax-lots`, `migration-plan`, and `GET /api/v1/connectors` remain available for narrow reads.

CSV still requires `account_id,symbol,transaction_date,quantity,price,type`; optional
`market_price,cost_basis,lot_id,external_id` improve valuation and tax-lot reconstruction.
Supported types are `BUY`, `SELL`, and `TRANSFER_IN`. Durable idempotency now comes from
PostgreSQL uniqueness on import content hashes and household-scoped source event IDs.

Run locally:

```bash
cd backend
python -m pip install -e .
uvicorn app.main:app --reload

# in another shell
cd frontend
npm install
npm run dev
```

Set `DATABASE_URL`, `LEDGERPILOT_AUTH_SECRET`, and optionally `LEDGERPILOT_FRONTEND_ORIGIN`
and `LEDGERPILOT_RAW_UPLOAD_ROOT` before starting the backend. The raw upload directory is
created outside the web root with restrictive filesystem permissions.

### Run the stack with Docker

From the repository root, run `docker compose -f infra/docker-compose.yml up --build`.
Then visit <http://localhost:3000>, register a profile, choose a household, upload a CSV, and
review holdings, tax lots, and migration guidance with visible `as_of`, source, freshness,
and sync-status indicators.

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
