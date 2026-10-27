"""Privacy-safe human-readable Numilog startup materialization status."""
from __future__ import annotations

from datetime import datetime
from time import time
from typing import Any


# The only per-book values accepted by the diagnostics boundary. Item
# positions refer exclusively to the order observed in that one private
# manifest; they are NOT stable provider IDs or cross-run book references.
_ITEM_LABELS = {
    "already_local_declared": "lien déclaré et fichier vérifiés",
    "declared_needs_review": "lien déclaré à revérifier",
    "already_local_verified": "fichier acquis antérieurement vérifié",
    "not_downloadable": "achat non téléchargeable",
    "needs_reconciliation": "reçu ou placement à rapprocher",
    "purchase_unproven": "droit d'achat non confirmé localement",
    "source_not_ready": "connexion non prête",
    "evidence_unavailable": "preuves locales indisponibles",
    "source_changed": "compte ou source modifiés",
    "invalid_source": "portée de source invalide",
    "invalid_item_id": "édition non qualifiée",
    "preflight_failed": "préflight non admissible",
    "consent_revoked": "consentement révoqué",
    "quota_reached": "quota de cette ouverture atteint",
    "acquired": "fichier acquis",
    "acquisition_failed": "acquisition non terminée",
}

_STATUS = {
    "never": "Aucun essai encore effectué",
    "running": "Sauvegarde en cours ou interrompue (non finalisée)",
    "complete": "Dernier essai terminé",
    "partial": "Dernier essai partiel : intervention possible",
    "failed": "Dernier essai en échec",
    "blocked": "Dernier essai bloqué ou révoqué",
    "unknown": "Dernier bilan indisponible",
    "not_due": "Aucun nouvel essai : délai minimum non écoulé",
}


def _date(value: int) -> str:
    return datetime.fromtimestamp(value).astimezone().strftime("%d/%m %H:%M")


def describe_run(mode: str, data: Any, *, now: int | None = None) -> str:
    """Display only exact allowlisted status/counters from the Core broker.

    Never format a raw provider response, error, title, credential or ID.
    """
    if mode not in {"manual", "on_start"} or type(data) is not dict:
        return ""
    status = data.get("status")
    if type(status) is not str or status not in _STATUS:
        return "Dernier bilan indisponible."
    started = data.get("started_at")
    due = data.get("next_eligible_at")
    counts = ("attempted", "downloaded", "skipped", "failed")
    if any(type(data.get(key)) is not int or data[key] < 0
           or data[key] > (2048 if key == "skipped" else 9 if key == "failed" else 8)
           for key in counts):
        return "Dernier bilan indisponible."
    # Optional account-scoped numbers exist only on recently finalized
    # receipts. Older v1 receipts remain readable, without invented counts.
    declared_keys = ("already_local_declared", "declared_needs_review")
    has_declared = any(key in data for key in declared_keys)
    if has_declared and (
            any(type(data.get(key)) is not int or not 0 <= data[key] <= 2048
                for key in declared_keys)
            or data["already_local_declared"] > data["skipped"]
            or (status == "complete" and data["declared_needs_review"] != 0)):
        return "Dernier bilan indisponible."
    # A manifest/acquisition failure can be followed by a *second* failure
    # while closing its durable receipt. Both must remain visible even when
    # no download was attempted (0 attempted, 2 failed). This agrees with
    # the closed Core StartupTrace contract without relaxing its other bounds.
    if (data["downloaded"] > data["attempted"]
            or data["failed"] > data["attempted"] + 2):
        # Never display internally contradictory counters as attested facts.
        return "Dernier bilan indisponible."
    if (started is not None and (type(started) is not int
            or not 1_000_000_000 <= started <= 9_999_999_999)
            or due is not None and (type(due) is not int
            or not 1_000_000_000 <= due <= 9_999_999_999)
            or (started is None and due is not None)
            or (started is not None and due != started + 3600)):
        return "Dernier bilan indisponible."

    # Auxiliary observation never changes the durable receipt or authorizes I/O.
    from ..core.numilog_startup_diagnostics import public_fields
    detail = public_fields(data.get("diagnostic"))
    if detail and detail["status"] == "complete" and status != "complete":
        # The auxiliary diagnostic never finalizes SQLite. A crashed or
        # stale worker may have emitted a "complete" diagnostic while the
        # durable receipt is still running/failed/unknown. Never turn that
        # observation into a public success or hide its unresolved state.
        detail = {}
    if detail and detail["status"] == "not_due" and status in {
        "running", "partial", "failed", "blocked",
    }:
        # A later rejected claim is NOT a replacement for the last actual
        # SQLite attempt. In particular, never display "0 errors" in place
        # of a failed materialization that consumed its one-hour claim.
        detail = {}
    if detail and status in {"complete", "partial", "failed", "blocked"}:
        if detail["status"] == "not_due":
            # Keep the existing no-op display for a *completed* prior run.
            pass
        elif (detail["status"] != status or
              any(detail[key] != data[key] for key in counts)):
            # At a finalized clock the SQLite receipt is authoritative.
            # Optional telemetry with a conflicting status or totals must
            # never rewrite its displayed truth.
            detail = {}
    if detail:
        status = detail["status"]
        data = dict(data, **{key: detail[key] for key in counts})
    caption = _STATUS[status]
    if started is not None and not detail:
        caption += f" le {_date(started)}"
    if status in {"complete", "partial", "failed", "blocked", "not_due"}:
        caption += (
            f" : {data['downloaded']} téléchargé(s), "
            f"{data['attempted']} tenté(s), "
            f"{data['skipped']} ignoré(s), {data['failed']} échec(s)"
        )
        if has_declared:
            caption += (
                f" ; {data['already_local_declared']} présence(s) déclarée(s) vérifiée(s)"
                f" ; {data['declared_needs_review']} liaison(s) déclarée(s) à revoir"
            )
    if detail:
        caption += f". Phase : {detail['cause_phase']} ; cause : {detail['code']}"
        if "task_ref" in detail:
            caption += f" ; tâche : {detail['task_ref']}"
    if detail and detail.get("item_decisions"):
        # Enumerated positions are non-authorizing: never imply that they
        # uniquely identify the same edition in a later manifest/GUI search.
        observed = detail["item_decisions"]
        caption += ". Achats examinés (rangs de cet inventaire uniquement) : "
        caption += " ; ".join(
            f"n°{row['position']} : {_ITEM_LABELS[row['decision']]}"
            for row in observed
        )
        if detail.get("items_truncated") is True:
            caption += " ; autres achats non détaillés"
    if mode == "on_start":
        current = int(time()) if now is None else now
        if type(current) is not int or current < 0:
            return "Dernier bilan indisponible."
        if due is not None and due > current:
            caption += (
                f". Prochain essai : après la prochaine ouverture, "
                f"pas avant le {_date(due)}"
            )
        else:
            caption += ". Prochain essai : à la prochaine ouverture"
    return caption + "."


def compact_run_caption(caption: str) -> str:
    """Readable source-card header; full diagnostics stay in a disclosure.

    Accept only the already validated allowlisted `describe_run` output,
    never a raw plugin exception or arbitrary book identifiers.
    """
    if type(caption) is not str or len(caption) > 2000:
        return "Dernier bilan indisponible."
    if not caption:
        return ""
    # Preserve terminal status + aggregate download/error counters. Individual
    # manifest positions, task references and internal phase codes belong in
    # the detailed, selectable GTK label, not the source-card subtitle.
    brief = caption
    for marker in (" ; ", ". Phase :", ". Achats examinés", ". Prochain essai"):
        brief = brief.split(marker, 1)[0]
    if len(brief) > 180:
        brief = brief[:177].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return brief
