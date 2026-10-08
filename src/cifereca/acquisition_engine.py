from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Protocol


EntitlementType = Literal["owned", "loan", "license", "unknown"]
EntitlementState = Literal["active", "realized", "inactive", "unknown"]
AcquisitionState = Literal[
    "discovered", "pending", "acquiring", "complete", "retryable_error", "attention_required"
]
Handling = Literal["http_plain", "adobe_adept", "kobo_kdrm", "unknown"]
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
class AcquisitionCandidate:
    candidate_id: str
    kind: str
    handling: Handling
    media_format: str
    source_format: str | None = None
    drm_family: str | None = None
    drm_raw_type: str | None = None
    transport: str = "https"
    method: str = "GET"
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    source: str | None = None
    display_profile: str | None = None
    provider_data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls,
        row: dict[str, Any],
        *,
        candidate_id: str | None = None,
        media_format: str | None = None,
        handling: str | None = None,
        drm_family: str | None = None,
        drm_raw_type: str | None = None,
        source: str | None = None,
        display_profile: str | None = None,
    ) -> "AcquisitionCandidate":
        drm = row.get("drm") if isinstance(row.get("drm"), dict) else {}
        kobo = row.get("kobo") if isinstance(row.get("kobo"), dict) else {}
        raw_headers = row.get("headers") if isinstance(row.get("headers"), dict) else {}
        headers = {str(k): str(v) for k, v in raw_headers.items()}
        resolved_media = str(media_format or row.get("media_format") or row.get("format") or "unknown").lower()
        resolved_handling = str(handling or row.get("handling") or "unknown").lower()
        if resolved_handling not in {"http_plain", "adobe_adept", "kobo_kdrm", "unknown"}:
            resolved_handling = "unknown"
        resolved_source = source or kobo.get("source") or row.get("source")
        resolved_profile = display_profile or kobo.get("display_profile") or row.get("display_profile")
        identifier = candidate_id or stable_id(
            "candidate",
            row.get("kind"),
            resolved_handling,
            resolved_media,
            row.get("source_format"),
            row.get("url"),
            resolved_source,
            resolved_profile,
        )
        return cls(
            candidate_id=identifier,
            kind=str(row.get("kind") or "http").lower(),
            handling=resolved_handling,  # type: ignore[arg-type]
            media_format=resolved_media,
            source_format=str(row.get("source_format") or "").strip() or None,
            drm_family=str(drm_family or drm.get("family") or row.get("drm_family") or "").strip() or None,
            drm_raw_type=str(drm_raw_type or drm.get("raw_type") or row.get("drm_type") or "").strip() or None,
            transport=str(row.get("transport") or "https").lower(),
            method=str(row.get("method") or "GET").upper(),
            url=str(row.get("url") or "").strip() or None,
            headers=headers,
            source=str(resolved_source or "").strip() or None,
            display_profile=str(resolved_profile or "").strip() or None,
            provider_data=dict(row),
        )

    def safe_shape(self) -> str:
        return "/".join(
            [
                f"{self.kind}:{self.media_format}",
                self.handling,
                self.drm_raw_type or self.drm_family or "?",
                self.source or "?",
                self.display_profile or "?",
            ]
        )


@dataclass(frozen=True)
class AcquisitionOffer:
    entitlement: Entitlement
    candidates: tuple[AcquisitionCandidate, ...]
    provider_contract: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def offer_id(self) -> str:
        return stable_id("offer", self.entitlement.entitlement_id, self.provider_contract, self.expires_at)


@dataclass(frozen=True)
class AcquisitionPlan:
    offer_id: str
    entitlement_id: str
    candidate: AcquisitionCandidate
    strategy: str
    executor: str
    requested_format: str
    reason: str


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


class AcquisitionExecutor(Protocol):
    executor_id: str

    def supports(self, plan: AcquisitionPlan) -> bool: ...

    def execute(self, plan: AcquisitionPlan) -> Artifact: ...


class AcquisitionPolicy:
    """Provider-neutral candidate ranking.

    Candidate *semantics* are carried by ``handling``. The default policy is the
    project policy already validated for Kobo: ADEPT licence first when usable,
    then plain HTTP, then KDRM. Unknown candidates are never silently executed.
    Providers may still pre-filter candidates that are not executable locally.
    """

    DEFAULT_PRIORITY: dict[str, int] = {
        "adobe_adept": 10,
        "http_plain": 20,
        "kobo_kdrm": 30,
        "unknown": 1000,
    }

    def __init__(self, priority: dict[str, int] | None = None) -> None:
        self.priority = dict(self.DEFAULT_PRIORITY)
        if priority:
            self.priority.update({str(k): int(v) for k, v in priority.items()})

    @staticmethod
    def _normalized_format(value: str | None) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"epub", "epub3", "kepub", "flepub"}:
            return "epub"
        if raw in {"pdf", "acsm"}:
            return raw
        return raw or "unknown"

    def rank(
        self,
        candidate: AcquisitionCandidate,
        *,
        preferred_format: str | None = None,
        allowed_handlings: Iterable[str] | None = None,
    ) -> tuple[int, int, int, str]:
        allowed = set(str(x) for x in allowed_handlings) if allowed_handlings is not None else None
        if allowed is not None and candidate.handling not in allowed:
            return (10000, 10000, 10000, candidate.candidate_id)
        handling_score = self.priority.get(candidate.handling, 1000)
        preferred = self._normalized_format(preferred_format)
        actual = self._normalized_format(candidate.media_format or candidate.source_format)
        format_score = 0 if not preferred_format or actual == preferred else 1
        # Prefer concrete media formats to unknown when all else is equal.
        known_score = 1 if actual == "unknown" else 0
        return (handling_score, format_score, known_score, candidate.candidate_id)

    def choose(
        self,
        candidates: Iterable[AcquisitionCandidate],
        *,
        preferred_format: str | None = None,
        allowed_handlings: Iterable[str] | None = None,
        predicate: Callable[[AcquisitionCandidate], bool] | None = None,
    ) -> AcquisitionCandidate | None:
        rows = [c for c in candidates if c.handling != "unknown"]
        if predicate is not None:
            rows = [c for c in rows if predicate(c)]
        if allowed_handlings is not None:
            allowed = {str(x) for x in allowed_handlings}
            rows = [c for c in rows if c.handling in allowed]
        if not rows:
            return None
        rows.sort(key=lambda c: self.rank(c, preferred_format=preferred_format, allowed_handlings=allowed_handlings))
        return rows[0]


class AcquisitionPlanner:
    def __init__(self, policy: AcquisitionPolicy | None = None) -> None:
        self.policy = policy or AcquisitionPolicy()

    def plan(
        self,
        offer: AcquisitionOffer,
        *,
        preferred_format: str | None = None,
        allowed_handlings: Iterable[str] | None = None,
        executor_for: Callable[[AcquisitionCandidate], str] | None = None,
        predicate: Callable[[AcquisitionCandidate], bool] | None = None,
    ) -> AcquisitionPlan | None:
        candidate = self.policy.choose(
            offer.candidates,
            preferred_format=preferred_format,
            allowed_handlings=allowed_handlings,
            predicate=predicate,
        )
        if candidate is None:
            return None
        requested_format = self.policy._normalized_format(candidate.media_format or candidate.source_format)
        if requested_format == "unknown" and preferred_format:
            requested_format = self.policy._normalized_format(preferred_format)
        executor = executor_for(candidate) if executor_for else "provider"
        strategy = candidate.handling
        return AcquisitionPlan(
            offer_id=offer.offer_id,
            entitlement_id=offer.entitlement.entitlement_id,
            candidate=candidate,
            strategy=strategy,
            executor=executor,
            requested_format=requested_format,
            reason=f"policy:{candidate.handling}",
        )


class AcquisitionLedger:
    """Small domain façade over Storage's generic ledger tables."""

    def __init__(self, storage: Any) -> None:
        self.storage = storage

    def discover(self, offer: AcquisitionOffer, *, title: str | None = None) -> str:
        self.storage.entitlement_put(offer.entitlement)
        acquisition_id = stable_id("acquisition", offer.entitlement.entitlement_id)
        self.storage.acquisition_put(
            {
                "acquisition_id": acquisition_id,
                "entitlement_id": offer.entitlement.entitlement_id,
                "provider_id": offer.entitlement.source.provider_id,
                "credential_id": offer.entitlement.source.credential_id,
                "account_id": offer.entitlement.source.account_id,
                "identity_id": offer.entitlement.source.identity_id,
                "item_id": offer.entitlement.item_id,
                "status": "discovered",
                "title": title,
                "metadata": {"offer_id": offer.offer_id, **offer.metadata},
            },
            insert_only=True,
        )
        return acquisition_id

    @staticmethod
    def _plan_metadata(plan: AcquisitionPlan) -> dict[str, Any]:
        return {
            "requested_format": plan.requested_format,
            "candidate_shape": plan.candidate.safe_shape(),
            "offer_id": plan.offer_id,
        }

    def set_plan(self, acquisition_id: str, plan: AcquisitionPlan) -> None:
        self.storage.acquisition_update_state(
            acquisition_id,
            "pending",
            strategy=plan.strategy,
            executor=plan.executor,
            candidate_id=plan.candidate.candidate_id,
            metadata=self._plan_metadata(plan),
        )

    def begin(
        self,
        acquisition_id: str,
        *,
        plan: AcquisitionPlan | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Begin an attempt, optionally replacing its plan atomically on retry."""
        self.storage.acquisition_update_state(
            acquisition_id,
            "acquiring",
            increment_attempt=True,
            strategy=plan.strategy if plan is not None else None,
            executor=plan.executor if plan is not None else None,
            candidate_id=plan.candidate.candidate_id if plan is not None else None,
            metadata={**(self._plan_metadata(plan) if plan is not None else {}), **(metadata or {})},
        )

    def complete(self, acquisition_id: str, artifact: Artifact) -> None:
        self.storage.artifact_put(artifact)
        self.storage.acquisition_update_state(
            acquisition_id,
            "complete",
            artifact_id=artifact.artifact_id,
            error_code=None,
            error_message=None,
            attention_code=None,
        )

    def fail(
        self,
        acquisition_id: str,
        *,
        code: str,
        message: str,
        retryable: bool = True,
        attention_code: str | None = None,
    ) -> None:
        state = "attention_required" if attention_code else ("retryable_error" if retryable else "attention_required")
        self.storage.acquisition_update_state(
            acquisition_id,
            state,
            error_code=code,
            error_message=message,
            attention_code=attention_code or (None if retryable else code),
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
