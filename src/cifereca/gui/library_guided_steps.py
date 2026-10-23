"""Closed, privacy-safe steps for the selected-pair reconciliation workflow.

No right, file, account, title or provider-originated string is rendered by
this guide. Pair suitability is decided exclusively by the Core preflight.
"""
from __future__ import annotations


_LABELS = {
    "choose_right": (
        "Étape 1/4 · Choisir un droit à examiner. "
        "Les droits non vérifiés sont consultables sans téléchargement."
    ),
    "choose_file": (
        "Étape 2/4 · Choisir un fichier local. "
        "Les suggestions bibliographiques sont des indices, jamais des preuves."
    ),
    "review_pair": (
        "Étape 3/4 · Examiner les preuves de la paire sélectionnée. "
        "Vous pouvez aussi déclarer un lien personnel, non attesté."
    ),
    "review_ready": (
        "Étape 3/4 · Vérifier les preuves actuelles, sans modifier la bibliothèque."
    ),
    "review_blocked": (
        "Étape 3/4 · Placement non autorisé avec ces preuves. "
        "Consultez le motif du préflight ; aucun fichier n'est absent par déduction."
    ),
    "confirm": (
        "Étape 4/4 · Les preuves ont été revalidées. "
        "Confirmez explicitement le placement, ou annulez sans écriture."
    ),
    "finished": (
        "Étape 4/4 · Placement local enregistré. "
        "Cette inspection est périmée : relancez-la pour examiner les preuves."
    ),
}
_PARIS = (
    "Étape 3/4 · Prêt de bibliothèque : consultation seule. "
    "Aucun placement fournisseur ne peut être confirmé ici."
)


def guided_stage_text(phase: str, *, provider: str | None = None) -> str:
    """Return only Core-authored labels for fixed states and the Paris loan."""
    if phase not in _LABELS:
        phase = "choose_right"
    if provider == "paris" and phase not in ("choose_right", "choose_file"):
        return _PARIS
    return _LABELS[phase]
