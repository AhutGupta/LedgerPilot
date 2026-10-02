from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol


class SecretProvider(Protocol):
    name: str

    def get_secret(self, reference: str) -> str | None: ...


class EnvSecretProvider:
    name = "env"

    def get_secret(self, reference: str) -> str | None:
        value = os.getenv(reference)
        return value if value else None


@dataclass
class StaticSecretProvider:
    values: dict[str, str]
    name: str = "static"

    def get_secret(self, reference: str) -> str | None:
        return self.values.get(reference)


class SecretProviderRegistry:
    def __init__(self, providers: list[SecretProvider]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    def get(self, provider_name: str) -> SecretProvider:
        try:
            return self._providers[provider_name]
        except KeyError as exc:
            raise ValueError(f"unsupported secret provider: {provider_name}") from exc


secret_provider_registry = SecretProviderRegistry([EnvSecretProvider()])
