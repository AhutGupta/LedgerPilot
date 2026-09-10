"""Private filesystem storage for encrypted raw CSV uploads."""

from __future__ import annotations

import os
from pathlib import Path

from app.config import get_settings
from app.services.secrets.crypto import EncryptedPayloadCodec, raw_upload_codec


class RawUploadStore:
    def __init__(self, root: Path | None = None, codec: EncryptedPayloadCodec | None = None) -> None:
        self.root = root or get_settings().raw_upload_root
        self.codec = codec or raw_upload_codec

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def store(self, household_id: str, household_person_id: str, connector: str, content_hash: str, content: str) -> str:
        self.ensure_root()
        household_dir = self.root / household_id / household_person_id
        household_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(household_dir, 0o700)
        target = household_dir / f"{connector}-{content_hash}.lpraw"
        if target.exists():
            return str(target.relative_to(self.root))
        file_descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            encrypted = self.codec.encrypt_text(
                content,
                aad={
                    "connector": connector,
                    "content_hash": content_hash,
                    "household_id": household_id,
                    "household_person_id": household_person_id,
                },
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="") as handle:
                handle.write(encrypted)
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return str(target.relative_to(self.root))

    def read(self, relative_path: str) -> str:
        return self.codec.decrypt_text((self.root / relative_path).read_text(encoding="utf-8"))


raw_upload_store = RawUploadStore()
