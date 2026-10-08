from __future__ import annotations


_ATTENTION_STATES = frozenset({"blocked", "error", "incompatible", "degraded"})

_RUNTIME_LABELS = {
    "ready": "Prêt",
    "disabled": "Disponible",
    "blocked": "Bloqué",
    "degraded": "Dégradé",
    "incompatible": "Incompatible",
    "error": "Erreur",
}


def activation_label(status: object) -> str:
    return "Activé" if bool(getattr(status, "enabled", False)) else "Désactivé"


def runtime_state_label(status: object) -> str:
    state = str(getattr(status, "state", "") or "")
    return _RUNTIME_LABELS.get(state, "État inconnu")


def status_needs_attention(status: object) -> bool:
    return str(getattr(status, "state", "") or "") in _ATTENTION_STATES


def freshness_label(package: dict) -> str:
    if package.get("builtin"):
        return ""
    status = str(package.get("freshness") or "unknown")
    reason = str(package.get("freshness_reason") or "")
    active = package.get("active")
    expected = package.get("expected_version")
    if status == "up_to_date":
        return f"Version · À jour · v{active}"
    if status == "outdated":
        return f"Version · Ancienne · v{active} · attendue v{expected}"
    if status == "newer_nonstandard":
        return f"Version · Plus récente / non standard · v{active} · attendue v{expected}"
    if reason == "not_installed" and expected:
        return f"Version · Non installée · attendue v{expected}"
    if reason == "inactive" and expected:
        return f"Version · Inactive · attendue v{expected}"
    if expected:
        return f"Version · État inconnu · attendue v{expected}"
    return "Version · Attendue inconnue"
