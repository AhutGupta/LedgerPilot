CREATE TABLE IF NOT EXISTS household_people (
    id TEXT PRIMARY KEY,
    household_id TEXT NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    full_name TEXT NOT NULL,
    linked_profile_id TEXT REFERENCES profiles(id) ON DELETE SET NULL,
    created_by_profile_id TEXT NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_household_people_household_linked_profile
    ON household_people (household_id, linked_profile_id)
    WHERE linked_profile_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_household_people_household_created_at
    ON household_people (household_id, created_at, id);

INSERT INTO household_people (id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at)
SELECT
    'person-' || h.id || '-' || h.created_by_profile_id,
    h.id,
    p.full_name,
    h.created_by_profile_id,
    h.created_by_profile_id,
    h.created_at
FROM households h
JOIN profiles p ON p.id = h.created_by_profile_id
WHERE NOT EXISTS (
    SELECT 1
    FROM household_people hp
    WHERE hp.household_id = h.id AND hp.linked_profile_id = h.created_by_profile_id
);

INSERT INTO household_people (id, household_id, full_name, linked_profile_id, created_by_profile_id, created_at)
SELECT
    'person-' || hm.household_id || '-' || hm.profile_id,
    hm.household_id,
    p.full_name,
    hm.profile_id,
    hm.profile_id,
    hm.joined_at
FROM household_memberships hm
JOIN profiles p ON p.id = hm.profile_id
WHERE NOT EXISTS (
    SELECT 1
    FROM household_people hp
    WHERE hp.household_id = hm.household_id AND hp.linked_profile_id = hm.profile_id
);

ALTER TABLE accounts ADD COLUMN IF NOT EXISTS household_person_id TEXT REFERENCES household_people(id) ON DELETE RESTRICT;
ALTER TABLE import_batches ADD COLUMN IF NOT EXISTS household_person_id TEXT REFERENCES household_people(id) ON DELETE RESTRICT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS household_person_id TEXT REFERENCES household_people(id) ON DELETE RESTRICT;
ALTER TABLE connector_links ADD COLUMN IF NOT EXISTS household_person_id TEXT REFERENCES household_people(id) ON DELETE RESTRICT;
ALTER TABLE sync_runs ADD COLUMN IF NOT EXISTS household_person_id TEXT REFERENCES household_people(id) ON DELETE RESTRICT;

UPDATE accounts a
SET household_person_id = COALESCE(
    (
        SELECT hp.id
        FROM household_people hp
        JOIN households h ON h.id = a.household_id
        WHERE hp.household_id = a.household_id AND hp.linked_profile_id = h.created_by_profile_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        WHERE hp.household_id = a.household_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    )
)
WHERE a.household_person_id IS NULL;

UPDATE import_batches ib
SET household_person_id = COALESCE(
    (
        SELECT hp.id
        FROM household_people hp
        JOIN households h ON h.id = ib.household_id
        WHERE hp.household_id = ib.household_id AND hp.linked_profile_id = h.created_by_profile_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        WHERE hp.household_id = ib.household_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    )
)
WHERE ib.household_person_id IS NULL;

UPDATE transactions t
SET household_person_id = COALESCE(
    (
        SELECT ib.household_person_id
        FROM import_batches ib
        WHERE ib.id = t.batch_id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        JOIN households h ON h.id = t.household_id
        WHERE hp.household_id = t.household_id AND hp.linked_profile_id = h.created_by_profile_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        WHERE hp.household_id = t.household_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    )
)
WHERE t.household_person_id IS NULL;

UPDATE connector_links cl
SET household_person_id = COALESCE(
    (
        SELECT hp.id
        FROM household_people hp
        JOIN households h ON h.id = cl.household_id
        WHERE hp.household_id = cl.household_id AND hp.linked_profile_id = h.created_by_profile_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        WHERE hp.household_id = cl.household_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    )
)
WHERE cl.household_person_id IS NULL;

UPDATE sync_runs sr
SET household_person_id = COALESCE(
    (
        SELECT cl.household_person_id
        FROM connector_links cl
        WHERE cl.id = sr.connector_link_id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        JOIN households h ON h.id = sr.household_id
        WHERE hp.household_id = sr.household_id AND hp.linked_profile_id = h.created_by_profile_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    ),
    (
        SELECT hp.id
        FROM household_people hp
        WHERE hp.household_id = sr.household_id
        ORDER BY hp.created_at, hp.id
        LIMIT 1
    )
)
WHERE sr.household_person_id IS NULL;

ALTER TABLE accounts ALTER COLUMN household_person_id SET NOT NULL;
ALTER TABLE import_batches ALTER COLUMN household_person_id SET NOT NULL;
ALTER TABLE transactions ALTER COLUMN household_person_id SET NOT NULL;
ALTER TABLE connector_links ALTER COLUMN household_person_id SET NOT NULL;
ALTER TABLE sync_runs ALTER COLUMN household_person_id SET NOT NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'accounts_household_id_external_account_id_key') THEN
        ALTER TABLE accounts DROP CONSTRAINT accounts_household_id_external_account_id_key;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'import_batches_household_id_connector_content_hash_key') THEN
        ALTER TABLE import_batches DROP CONSTRAINT import_batches_household_id_connector_content_hash_key;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transactions_household_id_source_id_key') THEN
        ALTER TABLE transactions DROP CONSTRAINT transactions_household_id_source_id_key;
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'connector_links_household_id_connector_key') THEN
        ALTER TABLE connector_links DROP CONSTRAINT connector_links_household_id_connector_key;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'accounts_household_person_external_account_key') THEN
        ALTER TABLE accounts
            ADD CONSTRAINT accounts_household_person_external_account_key
            UNIQUE (household_id, household_person_id, external_account_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'import_batches_household_person_connector_hash_key') THEN
        ALTER TABLE import_batches
            ADD CONSTRAINT import_batches_household_person_connector_hash_key
            UNIQUE (household_id, household_person_id, connector, content_hash);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'transactions_household_person_source_key') THEN
        ALTER TABLE transactions
            ADD CONSTRAINT transactions_household_person_source_key
            UNIQUE (household_id, household_person_id, source_id);
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'connector_links_household_person_connector_key') THEN
        ALTER TABLE connector_links
            ADD CONSTRAINT connector_links_household_person_connector_key
            UNIQUE (household_id, household_person_id, connector);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_accounts_household_person
    ON accounts (household_id, household_person_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_import_batches_household_person_imported_at
    ON import_batches (household_id, household_person_id, imported_at DESC);

CREATE INDEX IF NOT EXISTS idx_transactions_household_person_date
    ON transactions (household_id, household_person_id, transaction_date, symbol);

CREATE INDEX IF NOT EXISTS idx_connector_links_household_person_updated_at
    ON connector_links (household_id, household_person_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_sync_runs_household_person_started_at
    ON sync_runs (household_id, household_person_id, started_at DESC);
