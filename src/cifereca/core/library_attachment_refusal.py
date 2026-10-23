"""Core-owned, closed explanations for rejected manual attachment operations.

Only a known exception class with a known literal code can leave the private
transaction layer. Never stringify arbitrary exception arguments, account,
path, book, receipt, SHA, plugin text or provider responses. A reason explains
one refused attempt; it never grants a later attachment or undo authority.
"""
from __future__ import annotations

from .kobo_local_reconciliation import ReconciliationRejected
from .numilog_manual_link_transaction import ManualAttachmentRejected


_LABELS = {
    'numilog': {
        'purchase_provenance_unattested':
            "L’achat Numilog n’est pas attesté pour ce compte et cette édition.",
        'acquisition_ambiguous_or_missing':
            "La preuve d’acquisition est absente ou ambiguë pour cette édition.",
        'ambiguous_or_missing_right':
            "Le droit d’achat est absent ou ambigu dans la base actuelle.",
        'file_revalidation_failed':
            "Le fichier sélectionné ne passe plus le contrôle sécurisé.",
        'file_changed_before_commit':
            "Le fichier a changé avant la confirmation du placement.",
        'account_scope_changed':
            "Le compte fournisseur associé au droit a changé.",
        'source_scope_changed':
            "La connexion ou l’identité fournisseur a changé.",
        'placement_ambiguous':
            "Plusieurs placements incompatibles empêchent ce rattachement.",
        'catalogue_conflict':
            "Un lien de catalogue contredit le placement demandé.",
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
    'kobo': {
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
        'numilog': ManualAttachmentRejected,
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
