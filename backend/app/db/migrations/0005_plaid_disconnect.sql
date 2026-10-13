ALTER TABLE connector_links DROP CONSTRAINT IF EXISTS connector_links_status_check;
ALTER TABLE connector_links ADD CONSTRAINT connector_links_status_check CHECK (status IN ('pending', 'active', 'import_only', 'error', 'disconnected'));
