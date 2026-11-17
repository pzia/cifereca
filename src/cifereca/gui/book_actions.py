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
    """Return true only for a present source-attested local/storage copy.

    Historical remote purchase, loan and licence records cannot prove that
    the recipient already has the bytes, even with the same credential.
    The caller separately checks exact local placement and Core independently
    admits/revalidates any explicit copy. The credential keyword is retained
    for callers of the previous read-only projection.
    """
    target_identity = str(identity_id or "")
    provider = str(provider_id or "")
    item = str(source_item_id or "")
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
        if kind in {"local", "storage"} and state == "present":
            return True
    return False
