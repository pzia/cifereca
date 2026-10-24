"""Read-only visibility of configured Paris connections without a live account.

A failed provider login must not erase its *configuration* from the Sync UI.
This projection is intentionally separate from SyncSourceRef: a configured
credential is never a loan account, entitlement, or synchronization target.
No calls to connect(), providers or storage are made here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConfiguredConnectionNotice:
    provider_id: str
    state: str
    can_reconnect: bool


def unavailable_paris_connections(service: Any, identity_id: str) -> tuple[ConfiguredConnectionNotice, ...]:
    """Return bounded, non-identifying status rows for *this* identity only.

    Neither labels, borrower IDs, credentials nor provider exceptions are
    copied from config. A live account already has its ordinary SyncSourceRef,
    including its own disconnected state if the session later disappears.
    """
    if type(identity_id) is not str or not identity_id:
        return ()
    config = getattr(service, "config", None)
    reader = getattr(config, "provider_accounts", None)
    if not callable(reader):
        return ()
    try:
        configured = reader("paris")
        if type(configured) not in (list, tuple) or len(configured) > 128:
            return ()
        accounts_reader = getattr(service, "accounts_for_identity", None)
        accounts = accounts_reader(identity_id) if callable(accounts_reader) else ()
        if type(accounts) not in (list, tuple) or len(accounts) > 512:
            return ()
        live = {
            account.credential_id
            for account in accounts
            if getattr(account, "provider_id", None) == "paris"
            and getattr(account, "identity_id", None) == identity_id
            and type(getattr(account, "credential_id", None)) is str
        }
        registry = getattr(service, "registry", None)
        lookup = getattr(registry, "maybe_get", None)
        plugin = lookup("paris") if callable(lookup) else None
        plugin_ready = getattr(getattr(plugin, "descriptor", None), "state", None) == "ready"
        enabled_reader = getattr(service, "_provider_enabled_for_identity", None)
        provider_enabled = (enabled_reader("paris", identity_id) is not False
                            if callable(enabled_reader) else True)
    except Exception:
        # Never reflect raw exceptions: they may include account IDs and URLs.
        return ()

    notices: list[ConfiguredConnectionNotice] = []
    seen: set[str] = set()
    for row in configured:
        if type(row) is not dict:
            continue
        if row.get("identity_id") != identity_id:
            continue
        if str(row.get("provider") or "").strip().lower() != "paris":
            continue
        credential = row.get("id")
        credential = credential if type(credential) is str and 0 < len(credential) <= 256 else ""
        if credential and credential in live:
            continue
        # Deduplicate malformed config entries, without disclosing their keys.
        unique = credential or "__invalid__"
        if unique in seen:
            continue
        seen.add(unique)
        if not credential:
            state = "invalid_configuration"
        elif row.get("enabled", True) is not True:
            state = "account_disabled"
        elif plugin is None:
            state = "plugin_missing"
        elif not plugin_ready:
            state = "plugin_unavailable"
        elif not provider_enabled:
            state = "plugin_disabled"
        else:
            state = "not_connected"
        notices.append(ConfiguredConnectionNotice(
            provider_id="paris", state=state, can_reconnect=state == "not_connected",
        ))
        if len(notices) >= 16:
            break
    return tuple(notices)
