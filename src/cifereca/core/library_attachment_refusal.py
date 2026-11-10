"""Core-owned, closed explanations for rejected manual attachment operations.

Only a known exception class with a known literal code can leave the private
transaction layer. Never stringify arbitrary exception arguments, account,
path, book, receipt, SHA, plugin text or provider responses. A reason explains
one refused attempt; it never grants a later attachment or undo authority.
"""
from __future__ import annotations

from .local_attachment_refusal_types import ReconciliationRejected


_LABELS = {
    'numilog': {
        'numilog_canonical_redownload_required':
            "Rattachement historique indisponible : utilisez une nouvelle "
            "acquisition canonique si le droit actuel le permet.",
    },
    'kobo': {
        'kobo_canonical_evidence_required':
            "Kobo : le reçu historique ne prouve pas une livraison A→B. "
            "Rapprochement et annulation indisponibles ici. "
            "Utilisez le constat de fichier seul ou le relink canonique attesté.",
        'owned_right_required':
            "Le droit d’achat Kobo n’est pas attesté pour ce compte.",
        'active_right_ambiguous_or_missing':
            "Le droit Kobo actif est absent ou ambigu.",
        'backup_evidence_missing':
            "La sauvegarde Kobo antérieure nécessaire n’est pas attestée.",
        'revision_unproven':
            "La révision Kobo enregistrée n’est pas suffisamment prouvée.",
        'file_hash_mismatch':
            "Les octets du fichier ne correspondent pas à la sauvegarde Kobo.",
        'file_changed':
            "Le fichier a changé pendant sa vérification.",
        'source_configuration_changed':
            "La configuration de la connexion Kobo a changé.",
        'source_scope_mismatch':
            "Le compte ou le périmètre Kobo est incompatible.",
        'acquisition_evidence_mismatch':
            "Les preuves de l’acquisition Kobo sont contradictoires.",
        'local_link_conflict':
            "Un lien local incompatible empêche la restauration.",
        'preview_changed':
            "Les preuves ont changé depuis la prévisualisation.",
        'acquisition_busy':
            "Une autre acquisition ou réparation utilise actuellement le verrou.",
        'local_storage_unavailable':
            "La base locale ne permet pas de vérifier cette opération.",
        'schema_upgrade_required':
            "Une mise à niveau du schéma local est nécessaire.",
        'directory_migration_pending':
            "Une migration des répertoires du profil est en cours.",
    },
}


def public_refusal_code(provider: object, exc: BaseException) -> str | None:
    """Project only exact Core exception classes and literal allowlisted codes."""
    expected = {
        'kobo': ReconciliationRejected,
    }.get(provider if type(provider) is str else None)
    if expected is None or type(exc) is not expected:
        return None
    # Do not invoke __str__ or an arbitrary .code property on an exception.
    args = exc.args
    if type(args) is not tuple or len(args) != 1:
        return None
    code = args[0]
    if type(code) is not str:
        return None
    return code if code in _LABELS[provider] else None


def public_refusal_label(provider: object, code: object) -> str | None:
    """Only fixed, locally authored text; no free-form private error prose."""
    if type(provider) is not str or type(code) is not str:
        return None
    return _LABELS.get(provider, {}).get(code)
