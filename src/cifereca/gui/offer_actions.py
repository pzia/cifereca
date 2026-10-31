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
    """Conservative UX claim; only branding, never qualification, is vendor-specific."""
    if getattr(item, "source_provider", None) == "kobo":
        return ("Vérifier et sauvegarder via kobo-adept"
                if check_required and detailed_format
                else "Vérifier et sauvegarder" if check_required
                else "Sauvegarder via kobo-adept" if detailed_format
                else "Sauvegarder")
    if check_required:
        return "Vérifier et télécharger"
    return f"Télécharger {detailed_format}" if detailed_format else "Télécharger"
