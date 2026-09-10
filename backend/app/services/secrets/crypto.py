from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings
from app.services.secrets.provider import SecretProviderRegistry, secret_provider_registry


class EncryptedPayloadCodec:
    def __init__(
        self,
        *,
        secret_provider_name: str | None = None,
        secret_reference: str | None = None,
        provider_registry: SecretProviderRegistry | None = None,
        fallback_secret: str | None = None,
    ) -> None:
        settings = get_settings()
        self.secret_provider_name = secret_provider_name or settings.secret_provider
        self.secret_reference = secret_reference or settings.raw_encryption_secret_name
        self.provider_registry = provider_registry or secret_provider_registry
        self.fallback_secret = fallback_secret or settings.auth_secret

    def encrypt_text(self, plaintext: str, *, aad: dict[str, Any] | None = None) -> str:
        envelope = {
            "ciphertext": self._fernet().encrypt(
                json.dumps({"aad": aad or {}, "plaintext": plaintext}, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ).decode("utf-8"),
            "provider": self.secret_provider_name,
            "reference": self.secret_reference,
            "version": "lpv2",
        }
        return json.dumps(envelope, separators=(",", ":"), sort_keys=True)

    def decrypt_text(self, payload: str) -> str:
        envelope = json.loads(payload)
        if envelope.get("version") != "lpv2":
            raise ValueError("unsupported encrypted payload version")
        try:
            decrypted = self._fernet().decrypt(envelope["ciphertext"].encode("utf-8"))
            return json.loads(decrypted.decode("utf-8"))["plaintext"]
        except (InvalidToken, KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("encrypted payload integrity check failed") from exc

    def _fernet(self) -> Fernet:
        """Derive the Fernet key from a secret-provider value without persisting it."""
        provider = self.provider_registry.get(self.secret_provider_name)
        secret = provider.get_secret(self.secret_reference)
        return Fernet(_fernet_key(secret or self.fallback_secret))


raw_upload_codec = EncryptedPayloadCodec()


def _fernet_key(secret: str) -> bytes:
    import base64
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
