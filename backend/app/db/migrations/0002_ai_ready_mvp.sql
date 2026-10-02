CREATE TABLE IF NOT EXISTS connector_links (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    connector TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'import_only', 'error')),
    secret_provider TEXT NOT NULL,
    secret_reference TEXT,
    external_reference TEXT,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    last_synced_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (household_id, connector)
);

CREATE INDEX IF NOT EXISTS idx_connector_links_household_updated_at
    ON connector_links (household_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS sync_runs (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    connector_link_id TEXT REFERENCES connector_links(id) ON DELETE SET NULL,
    trigger TEXT NOT NULL CHECK (trigger IN ('import', 'manual', 'scheduled', 'ai_tool')),
    status TEXT NOT NULL CHECK (status IN ('pending', 'succeeded', 'skipped', 'failed', 'stale')),
    summary TEXT NOT NULL,
    stats JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_household_started_at
    ON sync_runs (household_id, started_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_policies (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    target_allocations JSONB NOT NULL DEFAULT '{}'::jsonb,
    rebalance_threshold_pct NUMERIC(10, 4) NOT NULL,
    cash_reserve_target_pct NUMERIC(10, 4) NOT NULL DEFAULT 0,
    max_single_position_pct NUMERIC(10, 4),
    notes TEXT,
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_policies_household_created_at
    ON portfolio_policies (household_id, created_at DESC);

CREATE TABLE IF NOT EXISTS memory_entries (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    entry_type TEXT NOT NULL CHECK (entry_type IN ('goal', 'constraint', 'preference', 'reconciliation_note')),
    content TEXT NOT NULL,
    labels JSONB NOT NULL DEFAULT '[]'::jsonb,
    importance TEXT NOT NULL CHECK (importance IN ('low', 'medium', 'high')),
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memory_entries_household_created_at
    ON memory_entries (household_id, created_at DESC);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_events_household_created_at
    ON audit_events (household_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    snapshot_type TEXT NOT NULL,
    source TEXT NOT NULL,
    freshness TEXT NOT NULL,
    sync_status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    based_on_sync_run_id TEXT REFERENCES sync_runs(id) ON DELETE SET NULL,
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_household_created_at
    ON portfolio_snapshots (household_id, created_at DESC);
