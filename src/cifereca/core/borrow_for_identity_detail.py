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
from .borrow_for_identity_progress import CrossProfileBorrowProgress


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


def cross_profile_borrow_progress_detail_section(
    progress: CrossProfileBorrowProgress,
) -> DetailSection | None:
    """Represent loan, delivery and copy as distinct non-actionable facts.

    The loan's confirmed state never implies an available local book or a
    transferable licence. The result intentionally carries only closed UI
    labels, not lender IDs, claim receipts, URLs or an executable callback.
    """
    if (type(progress) is not CrossProfileBorrowProgress
            or progress.executable is not False
            or progress.retry_borrow is not False):
        return None
    states = {
        ("not_attempted", "unavailable", "not_attempted",
         "loan_not_attempted"): (
            "Aucun emprunt effectué sur le profil prêteur",
            "Livraison non vérifiée",
            "Aucun transfert effectué",
        ),
        ("confirmed", "unavailable", "blocked",
         "lender_loan_confirmed_transfer_unverified"): (
            "Prêt confirmé sur le profil prêteur",
            "Livraison non vérifiée",
            "Copie en attente · livraison locale chez le prêteur non vérifiée",
        ),
        ("unknown", "unavailable", "blocked",
         "loan_result_not_confirmed"): (
            "Résultat du prêt inconnu · ne pas recommencer l'emprunt",
            "Livraison non vérifiée",
            "Copie vers le profil demandeur bloquée",
        ),
        ("unknown", "unavailable", "blocked",
         "lender_scope_unverified"): (
            "Profil prêteur non vérifié · statut du prêt inaccessible",
            "Livraison non vérifiée",
            "Copie vers le profil demandeur bloquée",
        ),
    }
    labels = states.get((
        progress.loan, progress.delivery, progress.transfer,
        progress.reason_code,
    ))
    if labels is None:
        return None
    return DetailSection(
        id="borrow-for-identity-progress",
        title="Emprunt entre profils · suivi",
        fields=(
            DetailField("borrow-for-identity-loan", "Prêt", labels[0]),
            DetailField("borrow-for-identity-delivery", "Livraison", labels[1]),
            DetailField("borrow-for-identity-transfer", "Transfert", labels[2]),
        ),
        order=951,
    )


__all__ = [
    "cross_profile_borrow_detail_section",
    "cross_profile_borrow_progress_detail_section",
]
