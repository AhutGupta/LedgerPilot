# LedgerPilot

LedgerPilot is a self-hosted, multi-user portfolio intelligence platform for households.
It is **read-only and advisory** (Observe → Analyze → Recommend → Explain), with no trade execution or money movement.

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
