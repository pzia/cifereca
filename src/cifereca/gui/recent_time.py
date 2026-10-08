from __future__ import annotations

from datetime import datetime
from typing import Any


def _timestamp_datetime(value: Any) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        timestamp = int(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp).astimezone()
    except (OverflowError, OSError, ValueError):
        return None


def format_recent_timestamp(
    value: Any,
    *,
    now: datetime | None = None,
    detailed: bool = False,
) -> str:
    """Format a materialization timestamp for the recent-acquisitions UI."""
    dt = _timestamp_datetime(value)
    if dt is None:
        return "Date inconnue"

    reference = (now or datetime.now().astimezone())
    if reference.tzinfo is None:
        reference = reference.astimezone()
    local = dt.astimezone(reference.tzinfo)
    delta_days = (reference.date() - local.date()).days

    if detailed:
        return local.strftime("%d/%m/%Y · %H:%M")
    if delta_days == 0:
        return f"Aujourd’hui · {local:%H:%M}"
    if delta_days == 1:
        return f"Hier · {local:%H:%M}"
    return local.strftime("%d/%m/%Y · %H:%M")
