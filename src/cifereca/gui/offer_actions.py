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
    item: Any, *, purchase_evidence=None, identity_id=None,
    account_context_epoch=None, source_context_epoch=None,
) -> bool:
    """Passive guard for an unqualified private inventory download hint.

    A supplier's owned/downloadable flag or general direct_download capability
    is not independent proof for a private library row without a delivery URL.
    Public provider download policies remain outside this private-only guard.
    """
    if getattr(item, "source_credential", None) == "__public__":
        return True
    if getattr(item, "download_url", None) is not None:
        return True
    if not (
        getattr(item, "owned", None) is True
        or getattr(item, "downloadable", None) is True
        or getattr(item, "source_collection", None) == "library"
    ):
        return True
    if getattr(item, "source_collection", None) != "library":
        return False
    from .query_model import result_row
    return result_row(
        item, purchase_evidence=purchase_evidence, identity_id=identity_id,
        account_context_epoch=account_context_epoch,
        source_context_epoch=source_context_epoch,
    )["state"] == "remote_owned"
