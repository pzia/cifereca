"""P0 #6366: exact private Paris account item -> public C N2 presentation.

The only authority is a transient Core receipt created on the *same*
account-scoped loan/history worker. Selecting/recycling GTK rows cannot
construct a source link, query SQLite, retrieve a catalogue or grant rights.
"""
from __future__ import annotations

from typing import Any, Callable

from ..models import HistoryItem, Loan
from ..core.loan_source_identity import loan_source_key
from ..core.notice_c_paris_personal import (
    VerifiedParisPersonalPublicC, valid_paris_personal_public_c,
)


def current_paris_personal_public_c(
    host: Any, item: Loan | HistoryItem,
) -> VerifiedParisPersonalPublicC | None:
    link = getattr(item, "_core_notice_c_paris_public_link", None)
    service = getattr(host, "service", None)
    identity = getattr(
        getattr(service, "current_identity", None), "identity_id", None,
    )
    if not valid_paris_personal_public_c(
        link, original=item, active_identity=identity,
        active_account=getattr(service, "current_account", None),
        account_epoch=getattr(service, "_account_context_epoch", None),
        source_epoch=getattr(service, "_source_context_epoch", None),
    ):
        return None
    if type(item) is Loan:
        live = getattr(host, "loan_objects", None)
        if (type(live) is not dict
                or live.get(loan_source_key(item)) is not item):
            return None
    elif type(item) is HistoryItem:
        live = getattr(host, "history_objects", None)
        if type(live) is not dict or live.get(item.id) is not item:
            return None
    else:
        return None
    return link


def public_c_is_current(host: Any, link: object) -> bool:
    return (
        type(link) is VerifiedParisPersonalPublicC
        and current_paris_personal_public_c(host, link.original) is link
    )


def show_paris_public_loan_cover(
    host: Any, source: Loan, display: Callable[[], Loan],
) -> bool:
    """Render original loan actions through its normal panel, C for image only."""
    link = current_paris_personal_public_c(host, source)
    if link is None:
        return False
    panel = getattr(getattr(host, "loan_page", None), "detail", None)
    renderer = getattr(panel, "show_loan_public_c", None)
    if not callable(renderer):
        return False
    preview = display()
    if type(preview) is not Loan:
        return False
    # Preview is a mutable, deliberately non-authoritative clone. Never
    # transfer the public C proof or any right to this displayed copy.
    if (preview.id != source.id
            or preview.record_id != source.record_id
            or preview.source_provider != source.source_provider
            or preview.source_credential != source.source_credential
            or preview.source_account_id != source.source_account_id):
        return False
    renderer(preview, public_link=link)
    return True


__all__ = [
    "current_paris_personal_public_c", "public_c_is_current",
    "show_paris_public_loan_cover",
]
