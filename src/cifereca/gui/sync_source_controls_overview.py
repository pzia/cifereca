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
    if type(sid) is not str or not sid:
        return "Réglages · portée du compte indisponible"

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
                parts.append("Notices et droits : " + mode)
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
            )
        else:
            parts.append("Fichiers : autorisation " + (
                "activée" if consent["enabled"] else "désactivée"
            ))

    if not parts:
        return ""
    if getattr(source, "enabled", None) is not True:
        parts.append("source désactivée")
    return "Réglages · " + " · ".join(parts)
