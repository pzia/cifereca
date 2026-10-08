from __future__ import annotations

from collections.abc import Iterable


def identity_has_attested_possession(
    possessions: Iterable[object],
    *,
    identity_id: str,
    provider_id: str,
    source_item_id: str,
    credential_id: str = "",
) -> bool:
    """Return true only for a current, source-attested possession.

    Remote rights must be active. Local/storage evidence must be present.
    When both sides expose a credential, it must match; when the source
    credential is known but the remote possession omits it, we stay
    conservative and keep the transfer action visible.
    """
    target_identity = str(identity_id or "")
    provider = str(provider_id or "")
    item = str(source_item_id or "")
    credential = str(credential_id or "")
    if not target_identity or not provider or not item:
        return False

    for possession in possessions:
        if str(getattr(possession, "identity_id", "") or "") != target_identity:
            continue
        if str(getattr(possession, "provider_id", "") or "") != provider:
            continue
        if str(getattr(possession, "source_item_id", "") or "") != item:
            continue

        kind = str(getattr(possession, "kind", "") or "")
        state = str(getattr(possession, "state", "") or "")
        if kind in {"local", "storage"}:
            if state == "present":
                return True
            continue
        if kind not in {"owned", "loan", "license"} or state != "active":
            continue

        if credential:
            facts = getattr(possession, "facts", {}) or {}
            possession_credential = str(
                facts.get("credential_id") if hasattr(facts, "get") else ""
            )
            if not possession_credential or possession_credential != credential:
                continue
        return True
    return False
