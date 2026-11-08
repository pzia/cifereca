"""Conservative, browser-resident SSO lifecycle contract for La Rochelle.

This state machine does not authenticate, launch Chromium, import browser
cookies, replay CAS tickets, or confirm a Core account. It provides stable
admission/expiry/cancellation semantics for a future explicitly consented
browser adapter. No URL or secret is stored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import secrets
from time import monotonic
from typing import Literal


Phase = Literal["idle", "pending", "browser_confirmed", "expired", "cancelled", "closed"]
MAX_AGE_SECONDS = 900


@dataclass(frozen=True, slots=True)
class SsoScope:
    identity_id: str
    credential_id: str
    provider_id: str = "biblioaccess.la-rochelle"
    tenant_id: str = "1409"

    def __post_init__(self):
        for value in (self.identity_id, self.credential_id):
            if (type(value) is not str or not 1 <= len(value) <= 256
                    or any(ord(char) < 33 or ord(char) == 127 for char in value)):
                raise ValueError("sso_invalid_scope")
        if (self.provider_id != "biblioaccess.la-rochelle"
                or self.tenant_id != "1409"):
            raise ValueError("sso_foreign_tenant")


@dataclass(slots=True)
class InteractiveSsoLifecycle:
    """No credentials, cookies, tickets, URLs or persisted browser state."""

    scope: SsoScope
    phase: Phase = "idle"
    _nonce: str = field(default="", repr=False)
    _deadline: float = field(default=0, repr=False)
    _browser_marker: object | None = field(default=None, repr=False)

    def begin(self, *, consent: bool, browser_marker: object, now: float | None = None) -> str:
        if (consent is not True or self.phase not in ("idle", "expired", "cancelled")
                or browser_marker is None
                or type(browser_marker) in (str, bytes, dict, list, tuple, int, float, bool)):
            raise ValueError("sso_explicit_consent_required")
        tick = monotonic() if now is None else now
        if type(tick) not in (int, float) or tick < 0 or tick != tick:
            raise ValueError("sso_invalid_clock")
        self._nonce = secrets.token_hex(16)
        self._deadline = tick + MAX_AGE_SECONDS
        # Object identity pins the ephemeral browser context; never serialise.
        self._browser_marker = browser_marker
        self.phase = "pending"
        return self._nonce

    def confirm_browser_observation(
        self, *, nonce: str, browser_marker: object, scope: SsoScope,
        page_is_authenticated: bool, now: float | None = None,
    ) -> str:
        """Browser-level evidence ONLY, not a live Cifereca ProviderSession."""
        tick = monotonic() if now is None else now
        if type(tick) not in (int, float) or tick != tick:
            return "rejected"
        if self.phase != "pending":
            return "rejected"
        if tick > self._deadline:
            self.invalidate("expired")
            return "expired"
        if (type(nonce) is not str or not secrets.compare_digest(self._nonce, nonce)
                or browser_marker is not self._browser_marker
                or type(scope) is not SsoScope or scope != self.scope
                or page_is_authenticated is not True):
            return "rejected"
        self.phase = "browser_confirmed"
        return "browser_confirmed"

    def live_browser(self, *, nonce: str, browser_marker: object,
                     scope: SsoScope, now: float | None = None) -> bool:
        tick = monotonic() if now is None else now
        if type(tick) not in (int, float) or tick != tick:
            return False
        if tick > self._deadline and self.phase == "browser_confirmed":
            self.invalidate("expired")
        return (
            self.phase == "browser_confirmed"
            and type(nonce) is str
            and secrets.compare_digest(self._nonce, nonce)
            and browser_marker is self._browser_marker
            and type(scope) is SsoScope
            and scope == self.scope
        )

    def invalidate(self, reason: Literal["cancelled", "expired", "closed"]) -> None:
        if reason not in ("cancelled", "expired", "closed"):
            raise ValueError("sso_invalid_invalidation")
        self.phase = reason
        self._nonce = ""
        self._deadline = 0
        self._browser_marker = None

    def diagnostic_state(self) -> dict[str, object]:
        return {"phase": self.phase, "tenant": "1409",
                "browser_only": True, "core_session_connected": False}
