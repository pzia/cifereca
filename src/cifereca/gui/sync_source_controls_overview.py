"""Read-only overview of the two independent per-source Sync controls (#3416).

Only structured Core broker state is projected. No provider text, credential,
account ID, entitlement or inferred catalogue-refresh right is ever rendered.
This module never writes policies or invokes a synchronization operation.
"""
from __future__ import annotations


def sync_source_controls_overview(source: object, broker: object) -> str:
    """Show inventory scheduling and file consent independently in the header.

    A missing/invalid read is *unknown*, never interpreted as OFF. A stored
    ON consent is not evidence of successful delivery or an enabled source.
    """
    if getattr(source, "sync_capable", None) is not True:
        return ""
    capabilities = getattr(source, "sync_capabilities", ())
    if (type(capabilities) not in (tuple, list) or len(capabilities) > 32
            or any(type(cap) is not str for cap in capabilities)):
        return "Réglages · état des capacités indisponible"

    sid = getattr(source, "source_id", None)
    if type(sid) is not str or not sid or len(sid) > 256:
        return "Réglages · portée du compte indisponible"

    # Match the effective/suspended semantics of the sticky dashboard.
    # Persisted ON is a preference, not an operational permission when the
    # selected source is disabled or its session is disconnected.
    source_enabled = getattr(source, "enabled", None) is True
    source_connected = getattr(source, "connected", None) is True
    available = source_enabled and source_connected
    parts = []
    if "remote_inventory" in capabilities:
        if "scheduled_refresh" in capabilities:
            reader = getattr(broker, "inventory_refresh_schedule", None)
            policy = None
            if callable(reader):
                try:
                    policy = reader(sid)
                except Exception:
                    pass  # Never surface private provider exception details.
            if (type(policy) is dict
                    and set(policy) == {"mode", "interval_minutes"}
                    and type(policy.get("mode")) is str
                    and policy["mode"] in {"manual", "on_start", "periodic"}
                    and type(policy.get("interval_minutes")) is int
                    and 15 <= policy["interval_minutes"] <= 10080):
                mode = {
                    "manual": "manuelle",
                    "on_start": "au démarrage",
                    "periodic": "périodique",
                }[policy["mode"]]
                parts.append(
                    "Notices et droits : " + mode
                    + (" (suspendue)" if mode != "manuelle" and not available else "")
                )
            else:
                parts.append("Notices et droits : réglage indisponible")
        else:
            parts.append("Notices et droits : planification non proposée")

    if "local_materialization" in capabilities:
        reader = getattr(broker, "source_sync_consent", None)
        consent = None
        if callable(reader):
            try:
                consent = reader(sid)
            except Exception:
                pass
        if (type(consent) is not dict
                or set(consent) != {"supported", "configured", "enabled", "revision", "valid"}
                or any(type(consent.get(key)) is not bool
                       for key in ("supported", "configured", "enabled", "valid"))
                or (consent["configured"] and
                    (type(consent["revision"]) is not str
                     or not 1 <= len(consent["revision"]) <= 128))
                or (not consent["configured"] and
                    (consent["revision"] is not None
                     or consent["enabled"] or not consent["valid"]))
                or (not consent["valid"] and consent["enabled"])):
            parts.append("Fichiers : autorisation non vérifiée")
        elif not consent["valid"]:
            parts.append("Fichiers : réglage à vérifier")
        elif not consent["configured"]:
            parts.append(
                "Fichiers : choix non défini" if consent["supported"]
                else "Fichiers : autorisation non proposée"
            )
        elif not consent["supported"]:
            # The broker permits revoking a formerly authorized policy even
            # when this version can no longer activate new deliveries.
            # Do not hide that still-persisted ON bit.
            parts.append(
                "Fichiers : autorisation antérieure " +
                ("activée" if consent["enabled"] else "désactivée") +
                " (activation indisponible)"
                + (" (suspendue)" if consent["enabled"] else "")
            )
        else:
            parts.append(
                "Fichiers : autorisation "
                + ("activée" if consent["enabled"] else "désactivée")
                + (" (suspendue)" if consent["enabled"] and not available else "")
            )

    if not parts:
        return ""
    if not source_enabled:
        parts.append("source désactivée")
    if not source_connected:
        parts.append("source déconnectée")
    return "Réglages · " + " · ".join(parts)


def sync_axes_dashboard_summary(sources: object, broker: object) -> str:
    """Summarize *effective* per-source synchronization settings, not rights.

    Scheduling and automatic file consent are independent axes. Counts refer
    only to sources available in the currently selected identity. A persisted
    ON choice on a disabled/disconnected source is *suspended*, not effective;
    a missing, malformed or failed local read is *unknown*, never OFF.
    Catalogue SEARCH does not imply a catalogue REFRESH capability.
    """
    if type(sources) not in (list, tuple) or len(sources) > 1024:
        return "Réglages par flux indisponibles"
    scheduled = manual = inventory_unknown = inventory_suspended = 0
    authorized = consent_suspended = consent_unknown = consent_unset = 0

    for source in sources:
        if getattr(source, "sync_capable", None) is not True:
            continue
        caps = getattr(source, "sync_capabilities", ())
        if (type(caps) not in (tuple, list) or len(caps) > 32
                or any(type(value) is not str for value in caps)):
            inventory_unknown += 1
            consent_unknown += 1
            continue
        sid = getattr(source, "source_id", None)
        scoped = type(sid) is str and bool(sid) and len(sid) <= 256
        available = (getattr(source, "enabled", None) is True
                     and getattr(source, "connected", None) is True)
        if "remote_inventory" in caps and "scheduled_refresh" in caps:
            policy = None
            reader = getattr(broker, "inventory_refresh_schedule", None)
            if scoped and callable(reader):
                try:
                    policy = reader(sid)
                except Exception:
                    pass
            if (type(policy) is not dict
                    or set(policy) != {"mode", "interval_minutes"}
                    or type(policy.get("mode")) is not str
                    or policy["mode"] not in {"manual", "on_start", "periodic"}
                    or type(policy.get("interval_minutes")) is not int
                    or not 15 <= policy["interval_minutes"] <= 10080):
                inventory_unknown += 1
            elif policy["mode"] == "manual":
                manual += 1
            elif available:
                scheduled += 1
            else:
                inventory_suspended += 1

        if "local_materialization" in caps:
            consent = None
            reader = getattr(broker, "source_sync_consent", None)
            if scoped and callable(reader):
                try:
                    consent = reader(sid)
                except Exception:
                    pass
            if (type(consent) is not dict
                    or set(consent) != {"supported", "configured", "enabled", "revision", "valid"}
                    or any(type(consent.get(key)) is not bool
                           for key in ("supported", "configured", "enabled", "valid"))
                    or (consent["configured"] and
                        (type(consent["revision"]) is not str
                         or not 1 <= len(consent["revision"]) <= 128))
                    or (not consent["configured"] and
                        (consent["revision"] is not None or consent["enabled"]
                         or not consent["valid"]))
                    or (not consent["valid"] and consent["enabled"])):
                consent_unknown += 1
            elif not consent["valid"]:
                consent_unknown += 1
            elif not consent["configured"]:
                if consent["supported"]:
                    consent_unset += 1
            elif consent["enabled"]:
                if consent["supported"] and available:
                    authorized += 1
                else:
                    consent_suspended += 1

    message = (
        f"Notices et droits : {scheduled} programmation(s) active(s)"
        f" · {manual} en manuel"
        f" · Réception de fichiers : {authorized} autorisation(s) effective(s)"
    )
    if inventory_suspended or consent_suspended:
        message += (
            f" · {inventory_suspended} programmation(s) suspendue(s)"
            f" · {consent_suspended} autorisation(s) suspendue(s)"
        )
    if inventory_unknown or consent_unknown or consent_unset:
        message += (
            f" · À vérifier : {inventory_unknown} programmation(s) inconnue(s)"
            f" · {consent_unknown} autorisation(s) inconnue(s)"
            f" · {consent_unset} choix de fichiers non défini(s)"
        )
    return message
