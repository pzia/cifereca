"""#4662: one neutral non-actionable detail section for Search/Recommendations.

BorrowForIdentityPreview is an inert Core read model. A lender name or a
catalogue score is not permission to borrow or copy; this projector produces
only DetailField text. The same DetailSection renderer may be shared by the
Search detail and recommendations, without a second GTK action engine.
"""
from __future__ import annotations

from ..plugin_api.models import DetailField, DetailSection
from .borrow_for_identity_preview import (
    BorrowForIdentityOption, BorrowForIdentityPreview, _display_account_label,
)


def cross_profile_borrow_detail_section(
    preview: BorrowForIdentityPreview,
) -> DetailSection | None:
    """Render 0/1/N observed lender routes as information, NEVER actions.

    No provider I/O, callback, URL, identifier token, purchase, supplier
    POST or copy. An invalid/forged preview is not displayed.
    """
    if (type(preview) is not BorrowForIdentityPreview
            or preview.executable is not False
            or type(preview.options) is not tuple
            or len(preview.options) > 64
            or any(type(option) is not BorrowForIdentityOption
                   or option.executable is not False
                   for option in preview.options)
            or preview.status not in {"none", "own", "external", "choose"}
            or (preview.status == "none") != (len(preview.options) == 0)
            or (preview.status in {"own", "external"}) !=
                (len(preview.options) == 1)
            or (preview.status == "choose") != (len(preview.options) > 1)
            or preview.requires_explicit_choice is not (
                preview.status == "choose"
            )):
        return None

    fields: list[DetailField] = [
        DetailField(
            "borrow-for-identity-state", "État",
            "Éligibilité personnelle et offre exacte non vérifiées"
            if preview.status == "none" else
            "Offres observées · à revalider avant tout emprunt",
        )
    ]
    size = len(preview.options)
    for n, option in enumerate(preview.options, start=1):
        # Option labels are not executable and are not trusted as sanitized
        # source text. Rebuild text from bounded, non-private display names.
        lender = _display_account_label(option.lender_name)
        if lender == "Compte bibliothèque":
            lender = "Profil prêteur"
        if option.borrowing_identity_id == preview.requesting_identity_id:
            lender = "Profil actif"
        account = _display_account_label(option.account_label)
        label = (f"Choix {n}/{size}" if size > 1 else "Prêteur possible")
        transfer = (
            "transfert bloqué" if option.transfer_state == "blocked" else
            "transfert non vérifié"
        )
        # Every choice is ordinal-distinct even when both accounts and
        # lender names have the same safe public display label.
        fields.append(DetailField(
            id=f"borrow-for-identity-choice-{n}",
            label=label,
            value=f"{lender} · {account} · {transfer}",
        ))

    # Never include option IDs, provider credentials, account unique IDs,
    # loan IDs, URLs, or a callback in a rendered detail.
    return DetailSection(
        id="borrow-for-identity",
        title="Emprunt entre profils · information",
        fields=tuple(fields),
        order=950,
    )


__all__ = ["cross_profile_borrow_detail_section"]
