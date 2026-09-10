# LedgerPilot

LedgerPilot is a self-hosted, multi-user portfolio intelligence platform for households.
It is **read-only and advisory** (Observe → Analyze → Recommend → Explain), with no trade execution or money movement.

## AI-ready LedgerPilot MVP (10 points)

The current MVP now delivers a compact, coherent backend for tenant-scoped dashboarding and AI-assisted analysis:

1. **Profile auth + tenant authorization** — bearer-authenticated users only see households they belong to.
2. **Canonical ledger ingestion** — CSV imports normalize into an authoritative household ledger with durable idempotency.
3. **Encrypted raw retention** — raw uploads are stored privately as encrypted envelopes, not plaintext CSVs.
4. **Secret-provider abstraction** — secret lookups are routed through pluggable providers (`env` now, registry-ready for others).
5. **Reusable dashboard application service** — dashboard, report, recommendation, and AI-tool reads reuse the same service layer.
6. **Policy persistence** — target allocations, rebalance thresholds, cash reserve targets, and concentration limits are versioned per household.
7. **Memory persistence** — household goals, constraints, preferences, and reconciliation notes are stored separately from canonical facts.
8. **Connector + sync persistence** — connector link state and sync runs are recorded even when refresh is import-driven.
9. **Snapshot + audit persistence** — dashboard snapshots and append-only audit events capture imports, policy/memory changes, syncs, and AI tool use.
10. **Deterministic finance tools** — holdings, tax lots, realized gains, allocation drift, cash deployment, sale simulation, and lightweight reports.

## Implemented API slice

### Auth and household scope

- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/me`
- `GET /api/v1/households`
- `POST /api/v1/households`

### Ledger, dashboard, and analysis

- `POST /api/v1/households/{household_id}/imports/{connector}`
- `GET /api/v1/households/{household_id}/dashboard`
- `GET /api/v1/households/{household_id}/holdings`
- `GET /api/v1/households/{household_id}/tax-lots`
- `POST /api/v1/households/{household_id}/simulate-sale`
- `GET /api/v1/households/{household_id}/recommendations`
- `GET /api/v1/households/{household_id}/reports/quarterly-review`
- `GET /api/v1/households/{household_id}/reports/ytd-realized-gains`

### Policy, memory, connector, sync, snapshot, audit

- `GET|POST /api/v1/households/{household_id}/policies`
- `GET|POST /api/v1/households/{household_id}/memory`
- `GET|POST /api/v1/households/{household_id}/connectors`
- `GET|POST /api/v1/households/{household_id}/syncs`
- `GET|POST /api/v1/households/{household_id}/snapshots`
- `GET /api/v1/households/{household_id}/audit`

### Tenant-authorized AI tool contract

AI clients stay on explicit tool rails and never receive direct database access.

- `GET /api/v1/households/{household_id}/ai/tools`
- `POST /api/v1/households/{household_id}/ai/tools/{tool_name}`

Current tool catalog:

- `get_dashboard`
- `get_portfolio_summary`
- `simulate_sale`
- `generate_report`
- `list_memory`
- `get_policy`

## CSV contract

CSV still requires `account_id,symbol,transaction_date,quantity,price,type`; optional
`market_price,cost_basis,lot_id,external_id` improve valuation and tax-lot reconstruction.
Supported types are `BUY`, `SELL`, and `TRANSFER_IN`.

## Product boundaries (MVP)

- ✅ Consolidate data from broker/bank connectors and file imports
- ✅ Normalize into a canonical ledger with provenance and auditability
- ✅ Run deterministic portfolio, tax, and report analysis
- ✅ Expose results in a read-only UI and via an AI assistant tool API
- ❌ No buy/sell/transfer execution APIs
- ❌ No direct AI database access

## Optimized data path

```text
Connectors / CSV
  -> Encrypted Raw Envelope + Provenance
  -> Normalizer
  -> Canonical Ledger (authoritative)
  -> Application Service
  -> Materialized Household Snapshots
  -> FastAPI
  -> Read-only UI / AI Tool Client
```

Each household read model carries: `as_of`, `source`, `freshness`, `sync_status`.

## Environment

Set these before starting the backend:

- `DATABASE_URL`
- `LEDGERPILOT_AUTH_SECRET`
- `LEDGERPILOT_FRONTEND_ORIGIN` (optional)
- `LEDGERPILOT_RAW_UPLOAD_ROOT` (optional)
- `LEDGERPILOT_SECRET_PROVIDER` (optional, defaults to `env`)
- `LEDGERPILOT_RAW_ENCRYPTION_SECRET_NAME` (optional, defaults to `LEDGERPILOT_RAW_UPLOAD_ENCRYPTION_KEY`)
- `LEDGERPILOT_RAW_UPLOAD_ENCRYPTION_KEY` (recommended for encrypted raw storage; backend falls back to the auth secret if omitted)

## Run locally

```bash
cd backend
python -m pip install -e .
uvicorn app.main:app --reload

# in another shell
cd frontend
npm install
npm run dev
```

Or run the stack with Docker:

```bash
docker compose -f infra/docker-compose.yml up --build
```

Then visit <http://localhost:3000>, register a profile, choose a household, upload a CSV,
and review holdings, tax lots, policy-driven recommendations, sync history, and dashboard metadata.
Only port `3000` is published to the host; the dashboard proxies `/api` requests to the private API
container, and PostgreSQL is available only within the Compose network.

Portfolio migration planning is intentionally deferred from the MVP and will be introduced as a future capability.
