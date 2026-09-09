from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    database_url: str
    raw_upload_root: Path
    frontend_origin: str
    auth_secret: str
    access_token_ttl_seconds: int


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    raw_upload_root = os.getenv("LEDGERPILOT_RAW_UPLOAD_ROOT", "data/raw-imports")
    raw_path = Path(raw_upload_root)
    if not raw_path.is_absolute():
        raw_path = (BASE_DIR / raw_path).resolve()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        database_url = (
            "postgresql://"
            f"{os.getenv('LEDGERPILOT_DB_USER', 'ledgerpilot')}:"
            f"{os.getenv('LEDGERPILOT_DB_PASSWORD', 'ledgerpilot')}@"
            f"{os.getenv('LEDGERPILOT_DB_HOST', 'localhost')}:"
            f"{os.getenv('LEDGERPILOT_DB_PORT', '5432')}/"
            f"{os.getenv('LEDGERPILOT_DB_NAME', 'ledgerpilot')}"
        )
    return Settings(
        database_url=database_url,
        raw_upload_root=raw_path,
        frontend_origin=os.getenv("LEDGERPILOT_FRONTEND_ORIGIN", "http://localhost:3000"),
        auth_secret=os.getenv("LEDGERPILOT_AUTH_SECRET", "ledgerpilot-dev-only-change-me"),
        access_token_ttl_seconds=int(os.getenv("LEDGERPILOT_ACCESS_TOKEN_TTL_SECONDS", "43200")),
    )
