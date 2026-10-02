CREATE TABLE IF NOT EXISTS plaid_items (
    connector_link_id TEXT PRIMARY KEY REFERENCES connector_links(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL UNIQUE,
    encrypted_access_token TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
