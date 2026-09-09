"""PostgreSQL connection and migration helpers."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import get_settings


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


@contextmanager
def get_connection() -> psycopg.Connection:
    with psycopg.connect(get_settings().database_url, row_factory=dict_row) as connection:
        yield connection


def run_migrations() -> None:
    with psycopg.connect(get_settings().database_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            applied = {
                row[0]
                for row in cursor.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
            }
            for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if migration.name in applied:
                    continue
                cursor.execute(migration.read_text(encoding="utf-8"))
                cursor.execute(
                    "INSERT INTO schema_migrations (version) VALUES (%s)",
                    (migration.name,),
                )
        connection.commit()
