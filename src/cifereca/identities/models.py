from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class IdentityPreferences:
    prefer_original_language: bool = False
    reading_languages: tuple[str, ...] = ("fr",)

    @classmethod
    def from_value(cls, value: Any) -> "IdentityPreferences":
        data = dict(value) if isinstance(value, dict) else {}
        raw = data.get("reading_languages", ["fr"])
        langs = tuple(
            str(x).strip().lower()
            for x in (raw if isinstance(raw, (list, tuple)) else [raw])
            if str(x).strip()
        )
        return cls(
            prefer_original_language=bool(data.get("prefer_original_language", False)),
            reading_languages=langs or ("fr",),
        )


@dataclass(frozen=True)
class Identity:
    identity_id: str
    label: str
    preferences: IdentityPreferences = field(default_factory=IdentityPreferences)
    metadata: dict[str, Any] = field(default_factory=dict)
    directory_name: str | None = None


@dataclass(frozen=True)
class ProviderConnection:
    connection_id: str
    identity_id: str
    provider_id: str
    credential_id: str
    label: str
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderAccountBinding:
    provider_account_id: str
    connection_id: str
    identity_id: str
    provider_id: str
    credential_id: str
    account_id: str
    label: str
    is_primary: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceBinding:
    device_id: str
    identity_id: str
    label: str | None = None
    enabled: bool = True
    target_directory: str = "Books/Paris"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DevicePlacement:
    device_id: str
    artifact_id: str
    desired_state: str = "keep"
    observed_state: str = "unknown"
    target_relative_path: str | None = None
    last_observed_at: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
