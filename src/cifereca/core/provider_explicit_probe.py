"""Explicit one-account provider diagnostic, isolated from startup sweep.

No Core provider-specific branches. This operation never connects all
configured sources or promotes a login just because a provider returned.
The caller must be an explicit user action selecting one persisted account.
The result is a closed projection, with no credential, exception or URL.
"""
from __future__ import annotations

from dataclasses import dataclass


class ExplicitProbeRefused(ValueError):
    """Stable non-sensitive refusal identifier."""


@dataclass(frozen=True)
class ExplicitProbeResult:
    provider_id: str
    status: str
    reason: str
    connected: bool = False


def run_explicit_provider_probe(
    service, *, identity_id: str, provider_id: str, credential_id: str,
    user_consent: bool,
) -> ExplicitProbeResult:
    """Run one configured interactive provider once, never other providers."""
    if user_consent is not True:
        raise ExplicitProbeRefused("explicit_probe_consent_required")
    coordinates = (identity_id, provider_id, credential_id)
    if any(type(x) is not str or not x or len(x) > 256
           or any(ord(c) < 33 or ord(c) == 127 for c in x)
           for x in coordinates):
        raise ExplicitProbeRefused("explicit_probe_invalid_scope")
    rows = service.config.provider_accounts(enabled_only=True)
    matched = [
        row for row in rows if type(row) is dict
        and row.get("provider") == provider_id
        and row.get("id") == credential_id
        and row.get("identity_id") == identity_id
    ]
    if len(matched) != 1:
        raise ExplicitProbeRefused("explicit_probe_source_not_unique")
    row = matched[0]
    plugin = service.registry.maybe_get(provider_id)
    if plugin is None or plugin.descriptor.state != "ready":
        raise ExplicitProbeRefused("explicit_probe_provider_unavailable")
    if getattr(plugin.descriptor, "interactive_authentication", False) is not True:
        raise ExplicitProbeRefused("explicit_probe_not_interactive")
    # For an HTTP-capable interactive provider the button is a one-shot
    # consent to TEST the saved account, even when background sync is OFF.
    # The descriptor declares the opt-in key; Core never knows provider IDs
    # or persists this overlay. No global reconnect or account registration.
    options = dict(row.get("options") or {})
    key = getattr(plugin.descriptor, "background_authentication_option_key", None)
    specs = getattr(plugin.descriptor, "account_options", ())
    if (type(key) is str and key and type(specs) is tuple
            and any(getattr(spec, "key", None) == key for spec in specs)):
        options[key] = "on"

    session = None
    try:
        session = plugin.create_session(
            credential_id=credential_id,
            label=str(row.get("label") or plugin.descriptor.display_name),
            credentials=dict(row.get("credentials") or {}),
            options=options,
            config=service.config, storage=service.storage,
            diagnostics=service.diagnostics,
        )
        # Result may raise deliberately after diagnostics are emitted. This
        # is NOT an authenticated account registration.
        try:
            session.connect()
        except Exception:
            return ExplicitProbeResult(
                provider_id=provider_id, status="observed",
                reason="provider_diagnostic_completed_unverified",
            )
        # Successful ephemeral authentication is visible as such, but does
        # not register the temporary account in the Core. The client is
        # closed by finally and cannot enter the automatic sync registry.
        if getattr(session, "connected", None) is True:
            accounts = getattr(session, "accounts", ())
            if isinstance(accounts, (tuple, list)) and accounts:
                return ExplicitProbeResult(
                    provider_id=provider_id, status="connected_ephemeral",
                    reason="provider_connection_test_succeeded_ephemeral",
                    connected=True,
                )
        return ExplicitProbeResult(
            provider_id=provider_id, status="observed",
            reason="provider_diagnostic_completed_unverified",
        )
    except Exception:
        # Never expose exception text: it may include a token or secret.
        return ExplicitProbeResult(
            provider_id=provider_id, status="failed",
            reason="provider_diagnostic_unavailable",
        )
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
