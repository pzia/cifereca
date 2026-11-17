"""Generic, read-only wording for selected-offer download actions (#3252).

The UI must not call provider snapshot/offer methods during rendering.
Method presence is only a capability hint: it never proves an owned
resource, selected offer, delivery, acquisition consent or local placement.
The Core action still owns every authorization and post-click check.
"""
from __future__ import annotations

from inspect import getattr_static
from typing import Any


_V2_SELECTED_OFFER_METHODS = (
    "downloadable_resource_snapshot",
    "downloadable_selected_offer_snapshot",
)


def requires_selected_offer_check(service: Any, item: Any) -> bool:
    """Is this source able to qualify an exact v2 offer *after* user action?

    Caller MUST already have obtained a positive, scoped Core download
    visibility decision. This function neither grants a button nor calls
    any provider method. In particular, a public SearchResult does not
    become eligible because a v2 method exists on the provider.
    """
    provider = getattr(item, "source_provider", None)
    credential = getattr(item, "source_credential", None)
    if (type(provider) is not str or not provider
            or type(credential) is not str or not credential
            or credential == "__public__"):
        return False
    try:
        session = service._session_for_source(provider, credential)
        return all(
            callable(getattr_static(session, name, None))
            for name in _V2_SELECTED_OFFER_METHODS
        )
    except Exception:
        # A source may disconnect between eligibility and this read-only hint.
        return False


def download_action_label(
    item: Any, *, check_required: bool, detailed_format: str = "",
) -> str:
    """Provider-neutral hint; qualification and consent remain in Core."""
    if check_required:
        return "Vérifier et télécharger"
    return f"Télécharger {detailed_format}" if detailed_format else "Télécharger"


def private_notice_download_hint_allowed(
    item: Any, *, service=None, identity_id=None,
    purchase_evidence=None, account_context_epoch=None, source_context_epoch=None,
) -> bool:
    """Passive guard for an unqualified private inventory download hint.

    A private library row without an explicit delivery URL needs the native
    Core selected-offer hint, not an old Entitlement. The compatibility keyword
    arguments cannot authorize this action. Public delivery is independent.
    A positive hint only permits the separate Core offer check after consent.
    """
    if getattr(item, "source_credential", None) == "__public__":
        return True
    if getattr(item, "download_url", None) is not None:
        return True
    # A scoped private account record needs independently verified proof.
    # An ordinary unscoped search notice can still enter the provider's
    # existing independent offer-check policy; a capability is not a grant.
    if not (
        getattr(item, "source_collection", None) == "library"
        or (getattr(item, "source_account_id", None)
            and (getattr(item, "owned", None) is True
                 or getattr(item, "downloadable", None) is True))
    ):
        return True
    if getattr(item, "source_collection", None) != "library":
        return False
    from ..core.private_inventory_row_scope import selected_private_offer_hint
    if (type(getattr(item, "metadata", None)) is dict
            and item.metadata.get("search_provenance") == "known_catalog_stale"):
        return False
    return selected_private_offer_hint(service, item, identity_id=identity_id) is True


def public_catalogue_download_hint_allowed(item: Any) -> bool:
    """Require a real public delivery offer, not just a catalogue listing.

    The `public_catalog` namespace is descriptive (not a purchase, a
    download grant or a source-specific allowlist). A selected public offer
    has to carry an exact URL and a positive downloadable signal before
    GTK may even consult the Core broker. Other public collections retain
    their existing independently qualified direct-download contracts.
    The Core still owns the final visibility and execution-time admission.
    """
    if getattr(item, "source_collection", None) != "public_catalog":
        return True
    link = getattr(item, "download_url", None)
    return (
        getattr(item, "source_credential", None) == "__public__"
        and getattr(item, "source_account_id", None) is None
        and getattr(item, "owned", None) is False
        and getattr(item, "downloadable", None) is True
        and type(link) is str and bool(link)
        and link == link.strip()
    )
