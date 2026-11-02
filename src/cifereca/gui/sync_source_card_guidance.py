"""Closed, provider-neutral guidance for a compact Sync source card (#3416).

Read-only presentation. Never construct a retry, permission, provider URL,
credential, account ID or an assertion that local files were received.
"""
from __future__ import annotations


def sync_source_card_guidance(source: object, alert: object) -> str:
    """An explicit next step only for a positively signalled problem.

    Source state precedes retry advice: a disconnected or disabled account
    cannot be repaired by pressing its unavailable Sync button. An
    informational partial-coverage note must never become an error CTA.
    """
    if getattr(source, "sync_capable", None) is not True:
        return ""
    if (type(alert) is not tuple or len(alert) != 2
            or alert[0] not in {"error", "warning"}):
        return ""
    if getattr(source, "enabled", None) is not True:
        return "À faire : activer cette source si vous souhaitez reprendre sa synchronisation."
    if getattr(source, "connected", None) is not True:
        return "À faire : reconnecter ce compte avant une nouvelle synchronisation."
    return (
        "À faire : examiner les détails du dernier cycle au survol du compte. "
        "La relance n'est pas une correction garantie."
    )


__all__ = ["sync_source_card_guidance"]
