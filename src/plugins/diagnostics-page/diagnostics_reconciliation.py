"""Read-only interpretation of verified Core library reconciliation events.

This page consumes only the public Diagnostics broker: no local files,
identifiers, account metadata or private Core modules. Unknown fields are
ignored; unknown causes are described generically, never echoed.
"""
from __future__ import annotations

from collections.abc import Mapping

_REASON_LABELS = {
    "audit_cancelled": "analyse interrompue",
    "cancelled": "inventaire interrompu",
    "snapshot_cancelled": "snapshot SQLite interrompu",
    "metadata_candidate_cancelled": "comparaison bibliographique interrompue",
    "audit_time_budget": "budget total dépassé",
    "time_budget": "budget de temps de l’inventaire atteint",
    "snapshot_time_budget": "lecture SQLite trop longue",
    "metadata_candidate_time_budget": "recherche bibliographique limitée par le temps",
    "hash_byte_budget": "budget SHA atteint",
    "file_byte_budget": "fichier trop volumineux",
    "entry_budget": "plafond d’entrées atteint",
    "file_budget": "plafond de fichiers atteint",
    "directory_unavailable": "répertoire non inspectable",
    "entry_unavailable": "entrée non inspectable",
    "root_changed": "racine modifiée",
    "file_changed": "fichier modifié pendant le contrôle",
    "epub_parser_limit_or_invalid": "EPUB illisible ou hors limites",
    "structure_limit_or_invalid": "structure invalide ou hors limites",
    "metadata_byte_budget": "budget métadonnées atteint",
    "comparison_budget": "budget de comparaison atteint",
    "metadata_candidate_row_budget": "plafond de notices atteint",
    "metadata_candidate_reference_budget": "plafond de références atteint",
    "metadata_candidate_keys_budget": "notice avec trop d’identifiants",
    "metadata_candidate_count_budget": "plafond de candidats atteint",
    "metadata_candidate_budget_or_cancelled": "recherche bibliographique incomplète",
    "stored_path_budget": "plafond de chemins enregistrés atteint",
    "stored_path_unavailable": "chemin enregistré non inspectable",
    "snapshot_row_budget": "plafond de lignes SQLite atteint",
    "snapshot_schema_incomplete": "schéma SQLite incomplet",
    "snapshot_byte_budget_or_invalid": "snapshot SQLite hors limites",
    "snapshot_unavailable": "snapshot SQLite indisponible",
    "snapshot_changed": "snapshot SQLite modifié pendant la lecture",
    "snapshot_reader_unavailable": "lecteur SQLite indisponible",
    "snapshot_wal_reader_unavailable": "lecteur WAL indisponible",
    "snapshot_identity_migration": "migration de bibliothèque en attente",
    "reconciliation_scope_changed": "identité ou portée modifiée",
    "reconciliation_identity_not_active": "identité inactive",
    "reconciliation_identity_root_collision": "racines d’identités chevauchantes",
    "reconciliation_unavailable": "réconciliation indisponible",
    "unclassified_limit": "cause non classée",
}
_STATUSES = {
    "complete": "Terminée",
    "partial": "Incomplète",
    "failed": "Indisponible",
    "cancelled": "Interrompue",
}
_COUNTS = (
    ("files_started", "fichiers commencés"),
    ("files_observed", "fichiers observés"),
    ("files_hashed", "fichiers SHA vérifié"),
    ("catalog_rows_examined", "notices examinées"),
    ("references_examined", "références examinées"),
    ("candidate_files_examined", "fichiers comparés"),
    ("metadata_candidates", "candidats bibliographiques"),
)
_TIMINGS = (
    ("snapshot", "Lecture SQLite/WAL"),
    ("sha256", "SHA"),
    ("epub_opf", "EPUB/OPF"),
    ("structure", "structure/CRC"),
    ("pdf_signature", "signature PDF"),
    ("candidate_index", "index catalogue"),
    ("candidate_match", "comparaison bibliographique"),
)


def _bounded(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 1_000_000_000_000 else None


def _numeric_line(raw: object, definitions: tuple, unit: str) -> str:
    if type(raw) is not dict:
        return ""
    pieces = []
    for key, label in definitions:
        value = _bounded(raw.get(key))
        if value is not None:
            pieces.append(f"{label} : {value}{unit}")
    return " · ".join(pieces)


def reconciliation_event_summary(raw: object) -> dict[str, str] | None:
    """Closed presentation; the summary is NOT a right or completeness proof."""
    if not isinstance(raw, Mapping):
        return None
    if (raw.get("source") != "core_events"
            or raw.get("module") != "LIBRARY"):
        return None
    detail = raw.get("detail")
    if type(detail) is not dict:
        return None
    if (type(detail.get("schema_version")) is not int
            or detail["schema_version"] != 1
            or detail.get("operation") != "library_reconciliation_audit"
            or detail.get("phase") != "finished"):
        return None
    status = detail.get("status")
    if type(status) is not str or status not in _STATUSES:
        return None
    reasons = detail.get("reasons")
    if (type(reasons) is not list or len(reasons) > 64
            or any(type(item) is not str for item in reasons)):
        return None
    # A malicious/older plugin should never make this presentation display
    # unverified cause strings, nor pretend an incomplete scan is complete.
    if (status == "complete" and reasons) or (status != "complete" and not reasons):
        return None
    code = detail.get("code")
    if (type(code) is not str
            or code != (sorted(set(reasons))[0] if reasons else "none")):
        return None
    known_code = code if code in _REASON_LABELS else ("none" if code == "none" else "unclassified_limit")
    known_reasons = [
        _REASON_LABELS.get(reason, "cause non classée")
        for reason in sorted(set(reasons))
    ]
    limit = (
        "Causes : " + " · ".join(known_reasons[:8])
        + (" · autres limites" if len(known_reasons) > 8 else "")
        if known_reasons else "Aucune limite signalée"
    )
    coverage = _numeric_line(detail.get("counts"), _COUNTS, "")
    timing = _numeric_line(detail.get("phase_ms"), _TIMINGS, " ms")
    elapsed = _bounded(detail.get("duration_ms"))
    if elapsed is not None:
        timing = ("durée totale : " + str(elapsed) + " ms"
                  + (" · " + timing if timing else ""))
    # Timings are elapsed phase aggregates, not a partition of wall time.
    note = ("Couverture partielle : les fichiers et droits non vérifiés "
            "ne sont pas réputés absents. Zéro candidat n’est pas une preuve "
            "d’absence." if status != "complete"
            else "Résultat local : aucune attribution de droit par simple ISBN/SHA.")
    return {
        "code": known_code,
        "state": _STATUSES[status],
        "coverage": coverage or "Couverture non mesurée",
        "timing": timing or "Durées non mesurées",
        "limits": limit,
        "note": note,
    }
