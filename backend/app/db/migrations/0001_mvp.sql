CREATE TABLE IF NOT EXISTS profiles (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS households (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS household_memberships (
    profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    joined_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, household_id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    external_account_id TEXT NOT NULL,
    connector TEXT NOT NULL,
    display_name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (household_id, external_account_id)
);

CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    connector TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_upload_path TEXT NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0 CHECK (row_count >= 0),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    UNIQUE (household_id, connector, content_hash)
);

CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL REFERENCES accounts(id) ON DELETE RESTRICT,
    account_external_id TEXT NOT NULL,
    symbol TEXT NOT NULL,
    transaction_date DATE NOT NULL,
    quantity NUMERIC(24, 8) NOT NULL,
    price NUMERIC(24, 8) NOT NULL,
    market_price NUMERIC(24, 8),
    transaction_type TEXT NOT NULL CHECK (transaction_type IN ('BUY', 'SELL', 'TRANSFER_IN')),
    cost_basis NUMERIC(24, 8) NOT NULL,
    lot_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (household_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_transactions_household_date
    ON transactions (household_id, transaction_date, symbol);

CREATE INDEX IF NOT EXISTS idx_transactions_household_account
    ON transactions (household_id, account_external_id);

CREATE INDEX IF NOT EXISTS idx_import_batches_household_imported_at
    ON import_batches (household_id, imported_at DESC);
