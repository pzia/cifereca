"""Storage value objects and legacy recovery, not an acquisition executor.

The old 0.8 offer/entitlement planner and writer have been retired. Canonical
A→B owns selected-offer admission and publication; loan occurrence records
and existing artifact/history recovery remain compatible.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


EntitlementType = Literal["owned", "loan", "license", "unknown"]
EntitlementState = Literal["active", "realized", "inactive", "unknown"]
ArtifactKind = Literal["file", "bundle"]


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\x1f".join(str(part or "") for part in parts).encode("utf-8", "surrogatepass")
    return f"{prefix}:{hashlib.sha256(raw).hexdigest()[:32]}"


@dataclass(frozen=True)
class SourceIdentity:
    provider_id: str
    credential_id: str
    account_id: str | None = None
    identity_id: str | None = None

    @property
    def key(self) -> str:
        return stable_id("source", self.provider_id, self.credential_id, self.account_id)


@dataclass(frozen=True)
class Entitlement:
    source: SourceIdentity
    item_id: str
    revision_id: str | None = None
    entitlement_type: EntitlementType = "owned"
    state: EntitlementState = "active"
    loan_id: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def entitlement_id(self) -> str:
        return stable_id(
            "entitlement",
            self.source.provider_id,
            self.source.credential_id,
            self.source.account_id,
            self.item_id,
            self.entitlement_type,
            self.loan_id,
        )


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    sha256: str
    size: int
    media_format: str
    mime_type: str | None = None
    path: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    kind: ArtifactKind = "file"

    @classmethod
    def from_file(
        cls,
        path: Path,
        *,
        sha256: str,
        media_format: str,
        mime_type: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Artifact":
        return cls(
            artifact_id=f"sha256:{sha256}",
            sha256=sha256,
            size=path.stat().st_size,
            media_format=media_format,
            kind="file",
            mime_type=mime_type,
            path=path,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_bundle(cls, manifest: Any, *, metadata: dict[str, Any] | None = None) -> "Artifact":
        merged = dict(getattr(manifest, "metadata", {}) or {})
        merged.update(dict(metadata or {}))
        members = getattr(manifest, "members", ()) or ()
        merged.setdefault("bundle_manifest", {
            "playlist": str(getattr(manifest, "playlist", "") or ""),
            "members": [member.as_dict() if hasattr(member, "as_dict") else dict(member) for member in members],
        })
        digest = str(getattr(manifest, "bundle_sha256"))
        return cls(
            artifact_id=f"bundle:{digest}",
            sha256=digest,
            size=int(getattr(manifest, "size", 0) or 0),
            media_format=str(getattr(manifest, "media_format", "bundle") or "bundle"),
            kind="bundle",
            mime_type="application/vnd.cifereca.bundle+json",
            path=Path(getattr(manifest, "root")),
            metadata=merged,
        )


class AcquisitionReconciler:
    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def recover_interrupted(self) -> int:
        """Convert stale in-flight rows into retryable work on startup.

        There is intentionally no daemon/lease in 0.8.0. A process-local worker
        owns the operation, so a persisted ``acquiring`` state after restart means
        the previous process was interrupted.
        """
        return self.storage.acquisition_recover_interrupted()

    def realize_expired_loans(self, *, now: int | None = None) -> int:
        return self.storage.entitlement_realize_expired(now=now)
