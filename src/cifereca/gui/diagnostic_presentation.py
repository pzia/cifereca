from __future__ import annotations

from typing import Any, Mapping


ATTENTION_STATES = frozenset({"blocked", "error", "incompatible"})

_STATE_LABELS = {
    "ready": "Prêt",
    "disabled": "Désactivé",
    "blocked": "Bloqué",
    "error": "Erreur",
    "incompatible": "Incompatible",
    "degraded": "Dégradé",
    "unknown": "État inconnu",
}

_ATTENTION_SUMMARIES = {
    "blocked": "Ce plugin est bloqué : une dépendance ou une configuration requise manque.",
    "error": "Ce plugin a rencontré une erreur et nécessite une vérification.",
    "incompatible": "Ce plugin n’est pas compatible avec cette version de Cifereca.",
}


def plugin_requires_attention(plugin: Mapping[str, Any]) -> bool:
    return str(plugin.get("state") or "unknown") in ATTENTION_STATES


def diagnostic_plugin_rows(
    data: Mapping[str, Any],
    *,
    attention_only: bool = False,
) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for raw in data.get("plugins") or ():
        if not isinstance(raw, Mapping):
            continue
        state = str(raw.get("state") or "unknown")
        attention = state in ATTENTION_STATES
        if attention_only and not attention:
            continue
        enabled = bool(raw.get("enabled"))
        label = _STATE_LABELS.get(state, state or "État inconnu")
        if not enabled and not attention:
            label = "Désactivé"
        summary = _ATTENTION_SUMMARIES.get(
            state,
            "Ce plugin est prêt à être utilisé." if state == "ready" and enabled
            else ("Ce plugin est désactivé." if not enabled else f"État : {label}."),
        )
        display_name = str(raw.get("display_name") or raw.get("plugin_id") or "Plugin")
        plugin_id = str(raw.get("plugin_id") or "")
        package_id = str(raw.get("package_id") or "")
        package_version = str(raw.get("package_version") or "")
        plugin_type = str(raw.get("type") or "")
        reason_code = str(raw.get("reason_code") or "")
        technical = " · ".join(
            part for part in (
                plugin_id,
                plugin_type,
                f"{package_id} {package_version}".strip(),
                f"raison={reason_code}" if reason_code else "",
            )
            if part
        )
        rows.append({
            "plugin_id": plugin_id,
            "display_name": display_name,
            "state": state,
            "state_label": label,
            "attention": attention,
            "summary": summary,
            "technical": technical,
        })
    rows.sort(key=lambda row: (
        0 if row["attention"] else 1,
        str(row["display_name"]).casefold(),
        str(row["plugin_id"]),
    ))
    return tuple(rows)


def diagnostic_attention_rows(data: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return diagnostic_plugin_rows(data, attention_only=True)
