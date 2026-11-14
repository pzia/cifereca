"""Closed Core-only, read-only review of retired manual source/file pairing.

A scanned local EPUB, an SQLite receipt or an ISBN is never a provider right,
an OriginLink, an acquisition or a permit. Canonical source delivery and
explicitly confirmed provenance live outside this retired pairing surface.
"""
from __future__ import annotations

from dataclasses import dataclass

_REASON_LABELS = {
    "selection_unavailable": "Sélection privée indisponible ou périmée.",
    "audit_incomplete": (
        "La couverture de l'inspection locale est incomplète ; "
        "aucune attribution de fichier à un droit n’est possible."
    ),
    "bibliographic_hint_only": (
        "Une notice ou un ISBN identique ne prouve ni un achat ni un rattachement."
    ),
    "loan_inactive_read_only": (
        "Prêt inactif ou terminé : consultation uniquement, sans placement, "
        "changement de licence ni restitution."
    ),
    "source_not_supported": (
        "Le rattachement manuel de fichier à un droit fournisseur est retiré. "
        "Utiliser la livraison canonique attestée de la source."
    ),
}


@dataclass(frozen=True, repr=False)
class ManualLinkPreflight:
    """Closed UI diagnostic, never an action or transaction token."""

    status: str
    codes: tuple[str, ...]
    read_only: bool = True
    attachment_authorized: bool = False
    requires_fresh_validation: bool = True

    @property
    def explanation(self) -> str:
        return "\n".join("• " + _REASON_LABELS[code] for code in self.codes)


def review_source_read_only(
    *, audit_complete: bool,
    bibliographic_hint: bool = False, loan_inactive: bool = False,
) -> ManualLinkPreflight:
    """Provider-neutral read-only diagnostic; no input can authorize a link."""
    reasons = {"source_not_supported"}
    if audit_complete is not True:
        reasons.add("audit_incomplete")
    if loan_inactive is True:
        reasons.add("loan_inactive_read_only")
    if bibliographic_hint is True:
        reasons.add("bibliographic_hint_only")
    return ManualLinkPreflight("blocked", tuple(sorted(reasons)))
