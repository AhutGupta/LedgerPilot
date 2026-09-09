"""Private filesystem storage for raw CSV uploads."""

from __future__ import annotations

import os
from pathlib import Path

from app.config import get_settings


class RawUploadStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or get_settings().raw_upload_root

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def store(self, household_id: str, connector: str, content_hash: str, content: str) -> str:
        self.ensure_root()
        household_dir = self.root / household_id
        household_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(household_dir, 0o700)
        target = household_dir / f"{connector}-{content_hash}.csv"
        if target.exists():
            return str(target.relative_to(self.root))
        file_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return str(target.relative_to(self.root))


raw_upload_store = RawUploadStore()
