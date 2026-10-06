from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


class AdeptProfileResolutionError(RuntimeError):
    def __init__(self, message: str, *, code: str = "adept_profile_required") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AdeptProfileResolution:
    profile_id: str
    source: str


class AdeptProfileResolver:
    """Resolve an ADE profile by provenance without owning ADE secrets.

    Precedence is deliberately stable: explicit acquisition override, Paris
    identity binding, unique fetch-capable external profile, otherwise an
    explicit user-action error. Provider sub-accounts never own ADE identity.
    """

    @staticmethod
    def _profile_id(row: dict[str, Any]) -> str | None:
        for key in ("profile_id", "id", "name", "profile"):
            value = str(row.get(key) or "").strip()
            if value:
                return value
        return None

    @staticmethod
    def _fetch_capable(row: dict[str, Any]) -> bool:
        provisioning = row.get("provisioning")
        if isinstance(provisioning, dict) and "acsm_fetch_ready" in provisioning:
            return bool(provisioning["acsm_fetch_ready"])
        for key in ("fetch_capable", "can_fetch", "fetch"):
            if key in row:
                return bool(row.get(key))
        capabilities = row.get("capabilities")
        if isinstance(capabilities, dict):
            for key in ("fetch", "acsm_fetch", "adept_fetch"):
                if key in capabilities:
                    return bool(capabilities.get(key))
        # Older Adept profile-list contracts intentionally permit arbitrary
        # profile objects. Treat an explicit authorization/readiness signal as
        # fetch-capable, but never assume capability solely from the profile name.
        for key in ("authorized", "ready", "usable"):
            if key in row:
                return bool(row.get(key))
        return False

    def resolve(
        self,
        *,
        explicit_profile: str | None = None,
        account_profile: str | None = None,
        identity_profile: str | None = None,
        profiles: Iterable[dict[str, Any]] = (),
    ) -> AdeptProfileResolution:
        for source, value in (
            ("acquisition", explicit_profile),
            ("identity", identity_profile),
        ):
            profile_id = str(value or "").strip()
            if profile_id:
                return AdeptProfileResolution(profile_id, source)

        capable: list[str] = []
        for raw in profiles:
            if not isinstance(raw, dict) or not self._fetch_capable(raw):
                continue
            profile_id = self._profile_id(raw)
            if profile_id and profile_id not in capable:
                capable.append(profile_id)
        if len(capable) == 1:
            return AdeptProfileResolution(capable[0], "unique_fetch_capable")
        if not capable:
            raise AdeptProfileResolutionError(
                "Aucun profil ADE fetch-capable n'est lié à cette acquisition ou cette identité",
                code="adept_profile_required",
            )
        raise AdeptProfileResolutionError(
            "Plusieurs profils ADE fetch-capable sont disponibles ; liez-en un à l'identité",
            code="adept_profile_ambiguous",
        )
