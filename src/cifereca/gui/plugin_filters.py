from __future__ import annotations

from collections.abc import Iterable


ATTENTION_STATES = frozenset({"blocked", "error", "incompatible", "degraded"})
FILTER_MODES = ("all", "active", "disabled", "attention")


def _text(value: object) -> str:
    return str(value or "").casefold()


def _query(value: object) -> str:
    return _text(value).strip()


def status_matches_mode(status: object, mode: str) -> bool:
    mode = mode if mode in FILTER_MODES else "all"
    if mode == "active":
        return bool(getattr(status, "enabled", False))
    if mode == "disabled":
        return not bool(getattr(status, "enabled", False))
    if mode == "attention":
        return str(getattr(status, "state", "") or "") in ATTENTION_STATES
    return True


def package_matches_query(package: dict, query: str) -> bool:
    needle = _query(query)
    if not needle:
        return True
    fields = (
        package.get("name"),
        package.get("package_id"),
        package.get("publisher"),
    )
    return any(needle in _text(value) for value in fields)


def _status_matches_query(status: object, query: str) -> bool:
    needle = _query(query)
    if not needle:
        return True
    descriptor = getattr(status, "descriptor", None)
    fields = (
        getattr(descriptor, "display_name", ""),
        getattr(descriptor, "id", ""),
        getattr(descriptor, "description", ""),
        getattr(descriptor, "package_id", ""),
        getattr(descriptor, "type", ""),
    )
    return any(needle in _text(value) for value in fields)


def filter_package_plugins(
    package: dict,
    statuses: Iterable[object],
    *,
    query: str = "",
    mode: str = "all",
) -> tuple[object, ...]:
    candidates = tuple(status for status in statuses if status_matches_mode(status, mode))
    if package_matches_query(package, query):
        return candidates
    return tuple(status for status in candidates if _status_matches_query(status, query))
