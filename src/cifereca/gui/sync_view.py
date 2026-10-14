from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SyncSourcePresentation:
    activation_label: str
    connection_label: str
    sync_label: str
    summary: str
    action_label: str
    action_enabled: bool
    disabled_reason: str


def _timestamp_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    return parsed.strftime("%d/%m/%Y %H:%M")


def sync_identity_action_label(identity: object | None) -> str:
    label = str(getattr(identity, "label", "") or "").strip() if identity is not None else ""
    return f"Synchroniser {label}" if label else "Synchroniser les sources"


def sync_source_presentation(source: object) -> SyncSourcePresentation:
    enabled = bool(getattr(source, "enabled", False))
    connected = bool(getattr(source, "connected", False))
    capable = bool(getattr(source, "sync_capable", False))
    label = str(getattr(source, "label", "") or getattr(source, "provider_id", "") or "cette source")

    activation = "Activée" if enabled else "Désactivée"
    connection = "Connectée" if connected else "Déconnectée"

    error = str(getattr(source, "error", "") or "").strip()
    status = str(getattr(source, "status", "") or "idle").strip().casefold()
    last_success = _timestamp_label(getattr(source, "last_success", ""))
    last_attempt = _timestamp_label(getattr(source, "last_attempt", ""))

    if not capable:
        sync_label = "Non prise en charge"
    elif status == "running":
        sync_label = "En cours…"
    elif status == "partial":
        sync_label = "Partielle" + (f" · {last_attempt}" if last_attempt else "")
    elif error or status in {"error", "failed"}:
        sync_label = "Échec" + (f" · {error}" if error else "")
    elif last_success:
        sync_label = f"Dernier succès {last_success}"
    elif last_attempt:
        sync_label = f"Dernier essai {last_attempt}"
    else:
        sync_label = "Jamais synchronisée"

    action_enabled = enabled and connected and capable
    if not enabled:
        disabled_reason = "Activez cette source pour la synchroniser."
    elif not connected:
        disabled_reason = "Reconnectez ce compte avant de synchroniser."
    elif not capable:
        disabled_reason = "Cette source ne fournit pas de synchronisation de données."
    else:
        disabled_reason = ""

    summary = (
        f"Activation : {activation} · Connexion : {connection}\n"
        f"Synchronisation : {sync_label}"
    )
    # A Kobo source refresh only updates online metadata. It is deliberately
    # NOT equivalent to the separate opt-in Kobo backup/acquisition operation.
    kobo_notices_only = getattr(source, "provider_id", "") == "kobo"
    if kobo_notices_only:
        summary += "\nActualise uniquement les notices Kobo en ligne ; ne télécharge aucun ebook."
        if "local_materialization" in getattr(source, "sync_capabilities", ()):
            summary += (
                "\nAcquisition locale Kobo disponible sur commande dédiée ; "
                "cette synchronisation ne la lance jamais."
            )
        else:
            summary += "\nAcquisition locale Kobo : non prise en charge par ce compte."
    if status in {"partial", "error", "failed"} and last_success:
        summary += f"\nDernier succès confirmé : {last_success}"
    if not action_enabled:
        summary += f"\nAction indisponible : {disabled_reason}"

    return SyncSourcePresentation(
        activation_label=activation,
        connection_label=connection,
        sync_label=sync_label,
        summary=summary,
        action_label=("Mettre à jour les notices Kobo en ligne" if kobo_notices_only
                      else f"Synchroniser {label}"),
        action_enabled=action_enabled,
        disabled_reason=disabled_reason,
    )
