from __future__ import annotations

from collections.abc import Iterable


PLUGIN_VIEW_FILTERS = (
    ("all", "Tous"),
    ("active", "Actifs"),
    ("disabled", "Désactivés"),
    ("attention", "À examiner"),
)

_ATTENTION_STATES = frozenset({"blocked", "error", "incompatible", "degraded"})


def _text(value: object) -> str:
    return str(value or "").casefold()


def package_matches_query(package: dict[str, object], query: str) -> bool:
    needle = str(query or "").strip().casefold()
    if not needle:
        return True
    haystack = " ".join(
        _text(package.get(key))
        for key in ("name", "package_id", "publisher")
    )
    return needle in haystack


def _status_matches_state(status: object, state_filter: str) -> bool:
    if state_filter == "all":
        return True
    enabled = bool(getattr(status, "enabled", False))
    state = str(getattr(status, "state", "") or "")
    if state_filter == "active":
        return enabled
    if state_filter == "disabled":
        return not enabled
    if state_filter == "attention":
        return state in _ATTENTION_STATES
    return True


def filter_package_plugins(
    package: dict[str, object],
    statuses: Iterable[object],
    *,
    query: str = "",
    state_filter: str = "all",
) -> list[object]:
    needle = str(query or "").strip().casefold()
    package_match = package_matches_query(package, needle)
    out: list[object] = []
    for status in statuses:
        if not _status_matches_state(status, state_filter):
            continue
        if package_match:
            out.append(status)
            continue
        descriptor = getattr(status, "descriptor", None)
        if descriptor is None:
            continue
        haystack = " ".join(
            _text(value)
            for value in (
                getattr(descriptor, "display_name", ""),
                getattr(descriptor, "description", ""),
                getattr(descriptor, "id", ""),
                getattr(descriptor, "package_id", ""),
            )
        )
        if needle in haystack:
            out.append(status)
    return out
