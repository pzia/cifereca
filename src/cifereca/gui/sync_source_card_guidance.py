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
    # Only explicit False attests OFF or a lost connection. A missing or
    # malformed Core state must not be presented as a request to enable or
    # reconnect; those actions may be inappropriate for this exact source.
    enabled = getattr(source, "enabled", None)
    if enabled is False:
        return "À faire : activer la source si souhaité."
    if enabled is not True:
        return "À faire : vérifier l’activation de cette source."
    connected = getattr(source, "connected", None)
    if connected is False:
        return "À faire : reconnecter ce compte."
    if connected is not True:
        return "À faire : vérifier la connexion de cette source."
    # Reuse ONLY the closed label emitted by CoreUI's phase-aware alert.
    # The 'offer' phase is a qualification step, not evidence that a
    # downloadable file has been selected or delivered. Never suggest an
    # automatic retry or a new acquisition from this diagnostic.
    if (
        type(alert[1]) is str
        and alert[1] in {
            "Offre de livraison : échec — action nécessaire",
            "Offre de livraison : bloqué — action nécessaire",
            "Offre de livraison : action nécessaire",
            "Offre de livraison : résultat partiel",
        }
    ):
        return "À faire : examiner la qualification des offres du dernier cycle."
    return "À faire : examiner le dernier cycle au survol."


__all__ = ["sync_source_card_guidance"]
