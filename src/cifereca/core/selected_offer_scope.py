"""Provider-neutral exact scope predicate for an already selected delivery offer.

This comparison alone grants no right, consent, download or publication. The
caller must independently verify the selected right and its delivery proof.
"""
from __future__ import annotations


def selected_offer_scope_matches(
    selected, *, identity_id: str, provider_id: str, credential_id: str,
    account_id: str, branch: str,
) -> bool:
    """Match the entire source owner and selected branch, fail closed on ABI gaps."""
    try:
        scope = selected.scope
        return (
            selected.branch == branch
            and scope.identity_id == identity_id
            and scope.provider_id == provider_id
            and scope.credential_id == credential_id
            and scope.account_id == account_id
        )
    except (AttributeError, TypeError, ValueError):
        return False


__all__ = ["selected_offer_scope_matches"]
