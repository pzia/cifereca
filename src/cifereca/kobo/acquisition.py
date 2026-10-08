from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlsplit

from ..acquisition_engine import (
    AcquisitionCandidate,
    AcquisitionOffer,
    AcquisitionPlanner,
    AcquisitionPolicy,
    Entitlement,
    SourceIdentity,
)


class KoboAcquisitionSelectionError(ValueError):
    pass


@dataclass(frozen=True)
class KoboOfferAdapter:
    """Translate Kobo Vault acquisition schema into the generic 0.8 model.

    Kobo-specific vocabulary remains here; the planner only sees normalized
    candidate handling/format semantics. This is intentionally independent of
    LibraryService so it is testable without sessions, SQLite or network I/O.
    """

    preferred_format: str = "epub"

    @staticmethod
    def adept_format(value: str | None) -> str | None:
        raw = str(value or "").strip().lower()
        if raw in {"epub", "epub3", "kepub", "flepub"}:
            return "epub"
        if raw == "pdf":
            return "pdf"
        return None

    @staticmethod
    def candidate_media_format(row: dict[str, Any]) -> str:
        values = (
            row.get("media_format"),
            row.get("format"),
            row.get("url_format"),
            row.get("source_format"),
        )
        fallback = ""
        for value in values:
            text = str(value or "").strip()
            if not text:
                continue
            if text.lower() != "unknown":
                return text
            fallback = fallback or text
        return fallback

    @staticmethod
    def canonical_drm(value: Any) -> str:
        raw = str(value or "").strip()
        folded = raw.casefold()
        if folded in {"signednodrm", "nodrm", "none"}:
            return "SignedNoDrm"
        if folded in {"kdrm", "kobo", "kobodrm", "kobo_kdrm"}:
            return "KDRM"
        if folded in {"adobedrm", "adobe_adept", "adept"}:
            return "AdobeDrm"
        return raw or "Unknown"

    @classmethod
    def candidate_drm(cls, row: dict[str, Any]) -> str:
        drm = row.get("drm")
        if isinstance(drm, dict):
            value = drm.get("raw_type") or drm.get("family")
        else:
            value = drm
        return cls.canonical_drm(value or row.get("drm_type") or row.get("drm_family"))

    @classmethod
    def candidate_handling(cls, row: dict[str, Any]) -> str:
        handling = str(row.get("handling") or "").strip().lower()
        if handling:
            return handling
        drm = cls.candidate_drm(row)
        if drm == "AdobeDrm":
            return "adobe_adept"
        if drm == "KDRM":
            return "kobo_kdrm"
        if drm == "SignedNoDrm":
            return "http_plain"
        return "unknown"

    @staticmethod
    def candidate_profile(row: dict[str, Any]) -> str | None:
        kobo = row.get("kobo")
        value = kobo.get("display_profile") if isinstance(kobo, dict) else None
        raw = str(value or "").strip()
        if not raw:
            raw = str(row.get("display_profile") or "").strip()
        return raw or None

    @staticmethod
    def candidate_source(row: dict[str, Any]) -> str | None:
        kobo = row.get("kobo")
        value = kobo.get("source") if isinstance(kobo, dict) else None
        raw = str(value or "").strip()
        if not raw:
            raw = str(row.get("source") or "").strip()
        return raw or None

    @staticmethod
    def candidate_requires_authorization(row: dict[str, Any]) -> bool:
        kobo = row.get("kobo")
        if isinstance(kobo, dict) and "requires_kobo_authorization" in kobo:
            return bool(kobo.get("requires_kobo_authorization"))
        return bool(row.get("requires_kobo_authorization"))

    @classmethod
    def normalize_candidate(cls, row: dict[str, Any]) -> AcquisitionCandidate:
        drm = row.get("drm") if isinstance(row.get("drm"), dict) else {}
        canonical = cls.candidate_drm(row)
        family = str(drm.get("family") or "").strip() or (
            "none" if canonical == "SignedNoDrm" else
            "kobo_kdrm" if canonical == "KDRM" else
            "adobe_adept" if canonical == "AdobeDrm" else "unknown"
        )
        return AcquisitionCandidate.from_mapping(
            row,
            media_format=cls.candidate_media_format(row) or "unknown",
            handling=cls.candidate_handling(row),
            drm_family=family,
            drm_raw_type=canonical,
            source=cls.candidate_source(row),
            display_profile=cls.candidate_profile(row),
        )

    @classmethod
    def candidates(cls, payload: dict[str, Any]) -> tuple[AcquisitionCandidate, ...]:
        acquisition = payload.get("acquisition")
        if not isinstance(acquisition, dict):
            raise KoboAcquisitionSelectionError("Kobo-Vault : réponse d'acquisition incomplète")
        raw = acquisition.get("candidates")
        if not isinstance(raw, list):
            raise KoboAcquisitionSelectionError("Kobo-Vault : aucun candidat d'acquisition")
        return tuple(cls.normalize_candidate(dict(row)) for row in raw if isinstance(row, dict))

    @classmethod
    def offer(
        cls,
        payload: dict[str, Any],
        *,
        credential_id: str,
        account_id: str | None,
        item_id: str,
        revision_id: str | None,
        entitlement_type: str = "owned",
        identity_id: str | None = None,
    ) -> AcquisitionOffer:
        acquisition = payload.get("acquisition") if isinstance(payload.get("acquisition"), dict) else {}
        validity = acquisition.get("validity") if isinstance(acquisition.get("validity"), dict) else {}
        entitlement = Entitlement(
            source=SourceIdentity("kobo", credential_id, account_id, identity_id),
            item_id=item_id,
            revision_id=revision_id,
            entitlement_type=entitlement_type if entitlement_type in {"owned", "loan", "license", "unknown"} else "unknown",  # type: ignore[arg-type]
            expires_at=str(validity.get("expires_at") or "").strip() or None,
            metadata={"provider": "kobo"},
        )
        return AcquisitionOffer(
            entitlement=entitlement,
            candidates=cls.candidates(payload),
            provider_contract=str(payload.get("schema_version") or "") or None,
            expires_at=entitlement.expires_at,
            metadata={"display_profile": acquisition.get("display_profile")},
        )

    @classmethod
    def _raw(cls, candidate: AcquisitionCandidate) -> dict[str, Any]:
        return dict(candidate.provider_data)

    @classmethod
    def acsm_candidate(cls, payload: dict[str, Any]) -> dict[str, Any] | None:
        candidates = cls.candidates(payload)
        usable = [
            c for c in candidates
            if c.handling == "adobe_adept"
            and (c.kind == "acsm" or c.media_format.lower() == "acsm")
            and c.transport == "https" and c.method == "GET" and str(c.url or "").startswith("https://")
        ]
        if not usable:
            return None
        usable.sort(key=lambda c: (0 if c.source == "library_download_link" else 1, c.candidate_id))
        return cls._raw(usable[0])

    @staticmethod
    def secure_http_candidate(candidate: AcquisitionCandidate) -> bool:
        """Accept executable HTTP downloads only over an explicit HTTPS GET URL."""
        if candidate.transport != "https" or candidate.method != "GET":
            return False
        url = candidate.url or ""
        if not url or any(ord(char) < 32 or ord(char) == 127 for char in url):
            return False
        try:
            parsed = urlsplit(url)
            # Accessing hostname/port validates malformed bracketed hosts/ports.
            return bool(
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            return False

    @classmethod
    def direct_candidate(cls, payload: dict[str, Any], preferred_format: str = "epub") -> dict[str, Any]:
        candidates = cls.candidates(payload)
        usable = [
            c for c in candidates
            if c.kind != "acsm"
            and cls.secure_http_candidate(c)
            and cls.adept_format(c.media_format) is not None
            and c.handling in {"http_plain", "kobo_kdrm"}
            and not (c.handling == "kobo_kdrm" and c.source == "library_sync")
        ]
        policy = AcquisitionPolicy(priority={"http_plain": 10, "kobo_kdrm": 20})
        chosen = policy.choose(usable, preferred_format=preferred_format)
        if chosen is None:
            shapes = ", ".join(c.safe_shape() for c in candidates[:6])
            suffix = f" (candidats: {shapes})" if shapes else ""
            raise KoboAcquisitionSelectionError(
                "Kobo-Vault ne fournit pas de candidat HTTP EPUB/PDF compatible avec le contrat Kobo-Adept négocié" + suffix
            )
        return cls._raw(chosen)

    @classmethod
    def delegated_candidate(cls, payload: dict[str, Any], preferred_format: str = "epub") -> dict[str, Any]:
        candidates = cls.candidates(payload)
        adobe = [c for c in candidates if c.handling == "adobe_adept" or c.drm_raw_type == "AdobeDrm"]
        usable = [
            c for c in candidates
            if c.kind != "acsm"
            and cls.adept_format(c.media_format) is not None
            and c.handling in {"http_plain", "kobo_kdrm"}
            # A delegated Kobo recipe may not expose a delivery URL yet.
            # A plain HTTP candidate does: never select it without HTTPS GET.
            and (c.handling == "kobo_kdrm" or cls.secure_http_candidate(c))
        ]
        policy = AcquisitionPolicy(priority={"http_plain": 10, "kobo_kdrm": 20})
        chosen = policy.choose(usable, preferred_format=preferred_format)
        if chosen is None:
            if adobe:
                raise KoboAcquisitionSelectionError(
                    "Kobo-Vault : le profil courant ne fournit qu'un artefact Adobe ; aucun ACSM exploitable n'a été sélectionné"
                )
            raise KoboAcquisitionSelectionError("Kobo-Vault : aucun candidat Kobo delegated EPUB/PDF exploitable")
        return cls._raw(chosen)

    @classmethod
    def generic_plan(
        cls,
        payload: dict[str, Any],
        *,
        credential_id: str,
        account_id: str | None,
        item_id: str,
        revision_id: str | None,
        preferred_format: str,
        allowed_handlings: Iterable[str] | None = None,
    ):
        offer = cls.offer(
            payload,
            credential_id=credential_id,
            account_id=account_id,
            item_id=item_id,
            revision_id=revision_id,
        )
        planner = AcquisitionPlanner()
        return offer, planner.plan(
            offer,
            preferred_format=preferred_format,
            allowed_handlings=allowed_handlings,
            executor_for=lambda c: "kobo-adept" if c.handling in {"adobe_adept", "kobo_kdrm", "http_plain"} else "provider",
        )
