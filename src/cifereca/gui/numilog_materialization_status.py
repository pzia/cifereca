"""Privacy-safe human-readable Numilog startup materialization status."""
from __future__ import annotations

from datetime import datetime
from time import time
from typing import Any


_STATUS = {
    "never": "Aucun essai encore effectué",
    "running": "Sauvegarde en cours ou interrompue (non finalisée)",
    "complete": "Dernier essai terminé",
    "partial": "Dernier essai partiel : intervention possible",
    "failed": "Dernier essai en échec",
    "blocked": "Dernier essai bloqué ou révoqué",
    "unknown": "Dernier bilan indisponible",
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
           or data[key] > (2048 if key == "skipped" else 8)
           for key in counts):
        return "Dernier bilan indisponible."
    if (data["downloaded"] > data["attempted"]
            or data["failed"] > data["attempted"] + 1):
        # Never display internally contradictory counters as attested facts.
        return "Dernier bilan indisponible."
    if (started is not None and (type(started) is not int
            or not 1_000_000_000 <= started <= 9_999_999_999)
            or due is not None and (type(due) is not int
            or not 1_000_000_000 <= due <= 9_999_999_999)
            or (started is None and due is not None)
            or (started is not None and due != started + 3600)):
        return "Dernier bilan indisponible."

    caption = _STATUS[status]
    if started is not None:
        caption += f" le {_date(started)}"
    if status in {"complete", "partial", "failed", "blocked"}:
        caption += (
            f" : {data['downloaded']} téléchargé(s), "
            f"{data['attempted']} tenté(s), "
            f"{data['skipped']} ignoré(s)"
        )
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
