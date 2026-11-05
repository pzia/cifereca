"""P1 #2854: reader-specific, human-readable USB dry-run decisions.

Pure, ephemeral presentation: no filesystem access, GTK, storage I/O,
provider requests, entitlement changes, mutation or execution capability.
Only the exact scoped Library book can receive a dry-run row. Every raw
error/filename/UUID/SHA is replaced with a closed public label.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

from cifereca.core.device_usb_dry_run import UsbDryRun
from cifereca.core.device_usb_preview import UsbPreviewRow
from .reader_library_projection import ReaderLibraryBook, ReaderLibrarySnapshot


_MAX_AGE_SECONDS = 300
_MAX_ROWS = 1000
_MAX_BOOKS = 30000
_ACTION_LABELS = {
    "upload_candidate": "Envoi à examiner · aucune copie effectuée",
    "skip": "Aucune opération nécessaire",
    "verify": "Vérification nécessaire · aucune opération",
    "blocked": "Bloqué · aucune opération autorisée",
}
_REASON_LABELS = {
    "revalidate_before_usb_write": "Livre local et absence observés ; autorisation d'envoi non vérifiée",
    "identical_sha_observed": "Empreintes identiques lors de cet examen",
    "already_missing": "Absence observée à la destination prévue",
    "device_observation_required": "Support non attesté ; présence inconnue",
    "target_path_unqualified": "Destination à définir avant tout envoi",
    "target_path_occupied": "Destination occupée par un autre fichier",
    "observed_target_path_mismatch": "Fichier repéré à un autre emplacement",
    "unmanaged_or_changed_file": "Fichier non géré ou modifié ; retrait interdit",
    "source_or_device_sha_unverified": "Empreintes locale/USB non concordantes",
    "owned_source_unverified": "Fichier local ou propriété non vérifiables",
    "invalid_intent": "Intention illisible",
    "device_disabled_or_unverified": "Liseuse désactivée ou non attestée",
    "reader_formats_unconfigured": "Formats acceptés non déclarés pour cette liseuse",
    "reader_format_unsupported": "Format du livre non déclaré compatible avec cette liseuse",
    "selected_variant_unverified": "Variante du fichier non qualifiée",
    "target_variant_suffix_mismatch": "Extension de destination incompatible avec la variante",
    "reader_scope_unverified": "Profil ou volume USB non vérifiable",
    "protection_reported": "Protection DRM signalée : transfert interdit",
    "drm_and_transfer_rights_unverified": "Format déclaré ; droits DRM et de copie non vérifiés",
    "content_inspection_size_limit": "Fichier trop volumineux pour la revue structurelle bornée",
    "content_inspection_budget_exhausted": "Budget de revue de contenu atteint ; relancer un examen ciblé",
    "content_source_unverified": "Source locale non vérifiable pour la revue de contenu",
    "content_bytes_unverified": "Octets locaux changés ou inaccessibles ; relancer l'examen",
    "content_structure_unverified": "Structure du fichier non reconnue ou invalide",
    "content_archive_unbounded": "Archive EPUB trop volumineuse après décompression",
    "content_protection_detected": "Protection détectée dans les octets ; envoi bloqué",
    "content_protection_reported": "Protection signalée dans les métadonnées ; envoi bloqué",
    "content_protection_unverified": "Métadonnées de protection non vérifiables",
    "epub_drm_and_transfer_rights_unverified": "Structure EPUB valide ; droits DRM et de copie non vérifiés",
    "kepub_semantics_drm_and_transfer_rights_unverified": "Structure EPUB valide ; spécificités KEPUB et droits de copie non vérifiés",
    "pdf_drm_and_transfer_rights_unverified": "Structure PDF valide ; DRM et droits de copie non vérifiés",
    "target_preflight_budget_exhausted": "Contrôles de destination limités pour cet examen ; reprendre par lots",
    "target_batch_capacity_insufficient": "Espace libre insuffisant pour tous les envois examinés",
    "target_scope_unverified": "Profil ou volume de destination non vérifiable",
    "target_directory_unverified": "Dossier géré sur le volume non vérifiable",
    "target_artifact_unverified": "Empreinte, taille ou variante locale incohérente",
    "target_intention_unverified": "Chemin réservé ou intention à requalifier",
    "target_platform_unsupported": "Contrôle physique impossible sur cette plateforme",
    "target_mount_unverified": "Volume USB réel non attesté",
    "target_directory_unbounded": "Trop de fichiers dans la destination gérée",
    "target_occupied": "Un fichier occupe déjà la destination prévue",
    "target_volume_read_only": "Volume monté en lecture seule",
    "target_space_unverified": "Espace libre du volume non mesurable",
    "target_space_insufficient": "Espace libre insuffisant avec la marge de sécurité",
    "target_directory_changed": "Dossier du support modifié pendant le contrôle",
    "target_mount_changed": "Volume USB changé pendant le contrôle",
    "target_io_unverified": "Accès au dossier de destination impossible",
}
_CURRENT = {
    "present": "Présence observée lors de cet examen",
    "missing": "Absence observée à la destination prévue",
    "unknown": "Présence non confirmée",
}
_FAILURE = "Aperçu périmé ou non vérifiable : relancez l'examen USB."


@dataclass(frozen=True, slots=True)
class ReaderUsbDecision:
    """A book list key and closed labels, never a mutation instruction."""

    book_key: str
    title: str
    state_label: str
    reason_label: str
    current_label: str
    executable: bool = False


@dataclass(frozen=True, slots=True)
class ReaderUsbDecisionDetails:
    identity_id: str
    device_id: str
    status: str
    observed_at: int | None
    books: tuple[ReaderUsbDecision, ...]
    message: str
    executable: bool = False


def _unverified(identity_id: str, device_id: str) -> ReaderUsbDecisionDetails:
    return ReaderUsbDecisionDetails(
        identity_id=identity_id, device_id=device_id,
        status="unverified", observed_at=None,
        books=(), message=_FAILURE,
    )


def project_reader_usb_decisions(
    *, report: UsbDryRun, library: ReaderLibrarySnapshot,
    now: int | None = None,
) -> ReaderUsbDecisionDetails:
    """Display only a recent, correctly scoped, fully non-executable report.

    Do not persist this value. The GTK caller must independently discard it
    on profile, reader, route, intent or library change; a completed dry-run
    is NEVER a future execution capability or live-presence cache.
    """
    if (type(library) is not ReaderLibrarySnapshot
            or type(report) is not UsbDryRun
            or type(library.identity_id) is not str
            or not library.identity_id
            or type(library.device_id) is not str
            or not library.device_id):
        raise ValueError("invalid reader decision presentation")
    identity_id, device_id = library.identity_id, library.device_id
    fail = lambda: _unverified(identity_id, device_id)
    clock = int(time.time()) if now is None else now
    if (type(clock) is not int or clock <= 0
            or type(report.identity_id) is not str
            or report.identity_id != identity_id
            or type(report.device_id) is not str
            or report.device_id != device_id
            or report.executable is not False
            or report.status != "scanned"
            or report.scan_attested is not True
            or type(report.observed_at) is not int
            or report.observed_at <= 0
            or report.observed_at > clock
            or clock - report.observed_at > _MAX_AGE_SECONDS
            or type(report.rows) is not tuple
            or len(report.rows) > _MAX_ROWS
            or type(library.books) is not tuple
            or len(library.books) > _MAX_BOOKS
            or library.current_presence_confirmed is not False):
        return fail()

    by_artifact: dict[str, UsbPreviewRow] = {}
    # Keep a closed one-to-one relation. A duplicated/corrupt report never
    # wins by list order and cannot silently become a book's current status.
    for row in report.rows:
        if (type(row) is not UsbPreviewRow
                or type(row.artifact_id) is not str
                or not row.artifact_id or row.artifact_id in by_artifact
                or row.executable is not False
                or row.desired not in ("keep", "remove")
                or type(row.desired) is not str
                or row.action not in _ACTION_LABELS
                or type(row.action) is not str
                or type(row.reason) is not str
                or row.observed not in _CURRENT
                or type(row.observed) is not str):
            return fail()
        # The Core currently never issues removals without a managed-write
        # receipt; a synthetic future remove_candidate must not be presented.
        by_artifact[row.artifact_id] = row
    # Library may be filtered, showing fewer books than a full device scan.
    # Unknown/hidden artifacts are ignored entirely (no title, SHA or path).
    seen_keys: set[str] = set()
    seen_artifacts: set[str] = set()
    entries: list[ReaderUsbDecision] = []
    for book in library.books:
        if (type(book) is not ReaderLibraryBook
                or type(book.key) is not str or not book.key
                or book.key in seen_keys
                or type(book.artifact_id) is not str or not book.artifact_id
                or book.artifact_id in seen_artifacts
                or book.desired_state not in ("none", "keep", "remove")
                or type(book.desired_state) is not str
                or book.current_presence != "unknown"):
            return fail()
        seen_keys.add(book.key)
        seen_artifacts.add(book.artifact_id)
        row = by_artifact.get(book.artifact_id)
        if book.desired_state == "none":
            if row is not None:
                return fail()
            continue
        if (row is None
                or row.desired != book.desired_state
                or row.last_known != book.last_known
                or row.last_observed_at != book.last_observed_at):
            return fail()
        # Recognize closed Core reasons only. Never print untrusted provider
        # text, raw paths or diagnostic identifiers from future consumers.
        reason = _REASON_LABELS.get(row.reason, "Vérification complémentaire nécessaire")
        if row.action == "upload_candidate" and row.observed != "missing":
            return fail()
        if row.action == "skip" and row.observed == "unknown":
            return fail()
        label = _ACTION_LABELS[row.action]
        entries.append(ReaderUsbDecision(
            book_key=book.key,
            title=book.title,
            state_label=label,
            reason_label=reason,
            current_label=_CURRENT[row.observed],
        ))
    return ReaderUsbDecisionDetails(
        identity_id=identity_id, device_id=device_id,
        status="scanned", observed_at=report.observed_at,
        books=tuple(entries),
        message=(
            f"Examen USB ponctuel : {len(entries)} livre(s) visible(s). "
            "Aucun transfert ou retrait. Toute opération exige une nouvelle vérification."
        ),
    )


__all__ = [
    "ReaderUsbDecision", "ReaderUsbDecisionDetails",
    "project_reader_usb_decisions",
]
