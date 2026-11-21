"""#6937: separate SQL reservation and physical stock figures in Maintenance."""
from __future__ import annotations

from ..core.derived_variant_maintenance_review import admit_maintenance_review
from ..core.derived_variant_capacity_review import read_capacity_review
from ..task_queue import TaskPriority

_FALLBACK = "Capacité indisponible ou contexte modifié. Aucun espace libre attesté."


def _subtitle(value):
    try:
        if (type(value) is not dict or value["status"] not in ("observed", "partial")
                or any(value[key] is not False for key in (
                    "admission_authorized", "recovery_authorized",
                    "quota_refund_authorized", "destructive_cleanup_allowed"))):
            return _FALLBACK
        counts, cap, stock = value["counts"], value["capacity"], value["stock"]
        if set(counts) != {"pending", "committed", "unreadable", "unbound",
                           "active_pending", "active_committed", "other_bound"}:
            return _FALLBACK
        numeric = (*counts.values(), *cap.values(), value["record_count"],
                   value["payload_bytes"], value["known_output_bytes"],
                   value["active_payload_bytes"], value["active_output_bytes"])
        if any(type(n) is not int or not 0 <= n <= 2**74 for n in numeric):
            return _FALLBACK
        if (set(cap) != {"records", "payload_bytes", "output_bytes"}
                or sum(counts[k] for k in ("pending", "committed")) != value["record_count"]
                or sum(counts[k] for k in ("unreadable", "unbound", "active_pending",
                                          "active_committed", "other_bound")) != value["record_count"]
                or type(value["output_bytes_complete"]) is not bool
                or value["output_bytes_complete"] != (counts["unreadable"] == 0)):
            return _FALLBACK
        limit = {"below_limits": "Sous les plafonds observés (pas une réservation).",
                 "at_limit": "Plafond atteint : nouvelle réservation non autorisée.",
                 "unknown": "Saturation indéterminée."}[value["saturation"]]
        physical = "Stock physique nommé : indisponible."
        if stock["status"] in ("journal_entries_observed", "partial"):
            logical, allocated = (stock["observed_unique_inode_logical_bytes"],
                                  stock["observed_unique_inode_allocated_bytes"])
            if any(type(n) is not int or not 0 <= n <= 2**74 for n in (logical, allocated)):
                return _FALLBACK
            physical = (f"Stock nommé du profil (inodes observés, non hachés) : "
                        f"{logical:,} octets logiques, {allocated:,} octets alloués. "
                        "Inventaire physique non exhaustif.")
        region_text = "Zone privée : observation indisponible ou incomplète."
        region = value.get("region")
        if type(region) is dict and region.get("status") == "observed":
            if any(region.get(key) is not False for key in (
                "exclusive_region_verified", "sha_verified", "external_dependencies_verified",
                "complete_physical_inventory", "reclaimable_bytes_known",
                "retention_authorized", "quota_refund_authorized", "destructive_cleanup_allowed",
            )) or region.get("bounded_walk_complete") is not True:
                return _FALLBACK
            rc = region["counts"]
            if (type(rc) is not dict
                    or set(rc) != {"pending_named", "committed_named", "unrecorded_regular",
                                  "ambiguous_entries", "unverified_regions", "unobserved_receipts"}
                    or any(type(n) is not int or not 0 <= n <= 2**20 for n in rc.values())):
                return _FALLBACK
            region_text = (
                f'Zone privée observée : {rc["pending_named"]} actifs, '
                f'{rc["committed_named"]} terminés, {rc["unrecorded_regular"]} sans reçu, '
                f'{rc["ambiguous_entries"]} entrées ambiguës, '
                f'{rc["unverified_regions"]} zones non vérifiées, '
                f'{rc["unobserved_receipts"]} reçus non observés. '
                "Aucun orphelin supprimable attesté."
            )
        compact_text = "Traces compactes : capacité indéterminée. Nouvelles réservations refusées tant que les témoins ne sont pas vérifiés."
        compact = value.get("compact")
        if compact is not None:
            if (type(compact) is not dict
                    or compact.get("retirement_authorized") is not False
                    or compact.get("quota_refund_authorized") is not False):
                return _FALLBACK
            if compact.get("status") in ("observed", "partial"):
                cc = compact["capacity"]
                n, used = compact["count"], compact["payload_bytes"]
                exact, unpaired = compact["matched"], compact["unpaired"]
                retired = compact.get("retired", 0)
                if (set(cc) != {"records", "payload_bytes"}
                        or any(type(k) is not int or not 0 <= k <= 2**74
                               for k in (n, used, exact, unpaired, retired, *cc.values()))
                        or n > cc["records"] or used > cc["payload_bytes"]
                        or exact + unpaired != n or retired > exact
                        or (compact["status"] == "observed") != (unpaired == 0)
                        or compact["saturation"] not in ("at_limit", "below_limits")
                        or (compact["saturation"] == "at_limit") != (
                            n >= cc["records"] or used >= cc["payload_bytes"])):
                    return _FALLBACK
                compact_limit = (" Plafond atteint." if compact["saturation"] == "at_limit"
                                 else "")
                compact_text = (
                    f'Traces compactes : {n}/{cc["records"]} témoins, '
                    f'{used:,}/{cc["payload_bytes"]:,} octets ; '
                    f'{exact} rattachés au reçu exact, {unpaired} non rattachés.'
                    f'{compact_limit} Capacité distincte. '
                    + (f'{retired} reçus en représentation compacte seule.'
                       if retired else 'Aucune retraite attestée.')
                )
                if unpaired:
                    compact_text += " Nouvelles réservations refusées : reçu original non vérifié."
            elif compact.get("status") != "unavailable":
                return _FALLBACK
        prefix = "" if value["output_bytes_complete"] else "Au moins "
        return (
            f'Installation : {value["record_count"]}/{cap["records"]} reçus '
            f'({counts["pending"]} actifs, {counts["committed"]} terminés), '
            f'{value["payload_bytes"]:,}/{cap["payload_bytes"]:,} octets de charge logique du journal.\n'
            f'{prefix}{value["known_output_bytes"]:,}/{cap["output_bytes"]:,} '
            f'octets déclarés réservés. {limit}\n'
            f'Profil actif : {counts["active_pending"]} actifs, {counts["active_committed"]} terminés, '
            f'{value["active_output_bytes"]:,} octets déclarés ; '
            f'{counts["unreadable"]} illisibles et {counts["unbound"]} non rattachables '
            'à l’installation.\n'
            f'{physical}\n{region_text}\n{compact_text} Aucun quota libéré, aucun octet récupérable attesté.'
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return _FALLBACK


def add_derived_variant_capacity_line(window, box, *, Adw):
    service = window.service
    group = Adw.PreferencesGroup()
    row = Adw.ActionRow()
    row.set_title("Capacité du journal des variantes")
    row.set_subtitle("Mesurer les réservations de l’installation et le stock nommé du profil.")
    button = window.action_button("Mesurer…", "system-search-symbolic")
    row.add_suffix(button)
    group.add(row)
    box.append(group)
    active = None

    def clicked(_button):
        nonlocal active
        if active is not None or getattr(window, "_closed", False) or button.get_root() is None:
            return
        request = admit_maintenance_review(service)
        if request is None or window.service is not service:
            return
        generation = getattr(window, "_identity_context_generation", None)
        route = getattr(window, "_route_display_token", None)
        token = active = object()
        row.set_subtitle("Mesure du journal en cours…")

        def done(value):
            nonlocal active
            if active is not token:
                return
            active = None
            if (not getattr(window, "_closed", False) and button.get_root() is not None
                    and window.service is service and request.current()
                    and getattr(window, "_identity_context_generation", None) == generation
                    and getattr(window, "_route_display_token", None) is route):
                row.set_subtitle(_subtitle(value))
            elif not getattr(window, "_closed", False) and button.get_root() is not None:
                row.set_subtitle(_FALLBACK)

        try:
            window.runner.submit(
                "Mesure de capacité du journal…",
                lambda: read_capacity_review(service, request),
                done, lambda _exc: done(None), control=button,
                priority=TaskPriority.MAINTENANCE, kind="maintenance-plan", origin="user",
            )
        except Exception:
            done(None)

    button.connect("clicked", clicked)
