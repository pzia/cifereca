"""#7093 presentation only: public observations and supervised dry-run."""
from cifereca.plugin_api import MaintenanceToolInfo, MaintenancePlan, MaintenanceResult
from cifereca.plugin_api.derived_maintenance import (
    DerivedPendingOverview, DerivedCapacityObservation, DerivedReceiptPage,
)


def overview_text(value):
    if type(value) is not DerivedPendingOverview:
        return "Revue indisponible. Aucun constat d’absence."
    coverage = "Page des reçus examinée." if value.complete_pending_page else "Revue partielle."
    return (f"{value.checked} reçu(s) examinés : {value.linked_pending} déjà liés, "
            f"{value.staged_only} en staging, {value.manual_review} à vérifier. "
            f"{coverage} Aucune reprise ni suppression autorisée.")


def capacity_text(value):
    if type(value) is not DerivedCapacityObservation:
        return "Capacité indisponible. Aucun espace libre attesté."
    c, cap = dict(value.counts), dict(value.capacity)
    saturation = {"below_limits": "Sous les plafonds observés (pas une réservation).",
                  "at_limit": "Plafond atteint.", "unknown": "Saturation indéterminée."}
    physical = "Stock nommé indisponible."
    if value.logical_bytes is not None and value.allocated_bytes is not None:
        physical = (f"Stock nommé du profil : {value.logical_bytes:,} octets logiques, "
                    f"{value.allocated_bytes:,} alloués ; inventaire non exhaustif, non haché.")
    region = "Zone privée : observation indisponible ou incomplète."
    if value.region_status == "observed":
        r = dict(value.region_counts)
        region = (f"Zone privée : {r['pending_named']} actifs, {r['committed_named']} terminés, "
                  f"{r['unrecorded_regular']} sans reçu, {r['ambiguous_entries']} entrées ambiguës, "
                  f"{r['unverified_regions']} zones non vérifiées, "
                  f"{r['unobserved_receipts']} reçus non observés. Aucun orphelin supprimable attesté.")
    compact = "Traces compactes : capacité indéterminée."
    if value.compact_status in ("observed", "partial"):
        cc, limits = dict(value.compact_counts), dict(value.compact_capacity)
        compact = (f"Traces compactes : {cc['count']}/{limits['records']} témoins, "
                   f"{cc['payload_bytes']:,}/{limits['payload_bytes']:,} octets ; "
                   f"{cc['matched']} rattachés, {cc['unpaired']} non rattachés. "
                   "Capacité distincte, aucune retraite attestée.")
        if value.compact_saturation == "at_limit":
            compact += " Plafond atteint."
    prefix = "" if value.output_bytes_complete else "Au moins "
    return (f"Installation : {value.record_count}/{cap['records']} reçus "
            f"({c['pending']} actifs, {c['committed']} terminés) ; "
            f"{value.payload_bytes:,}/{cap['payload_bytes']:,} octets de journal.\n"
            f"{prefix}{value.known_output_bytes:,}/{cap['output_bytes']:,} octets déclarés réservés. "
            f"{saturation[value.saturation]}\n"
            f"Profil actif : {c['active_pending']} actifs, {c['active_committed']} terminés, "
            f"{value.active_output_bytes:,} octets déclarés ; {c['unreadable']} illisibles "
            f"et {c['unbound']} non rattachables à l’installation.\n"
            f"{physical}\n{region}\n{compact}\n"
            "Aucun quota libéré, aucun octet récupérable attesté.")


class DerivedController:
    def __init__(self, broker, host, active, render):
        self.broker, self.host, self.active, self.render = broker, host, active, render
        self.identity = host.identity_id
        self.page = self.reviewed = self.choice = None
        self.busy = self.disposed = False

    def current(self):
        return (not self.disposed and self.active() and self.host.is_current()
                and self.host.identity_id == self.identity)

    def request(self, action, *, choice=None, confirmed=False):
        if self.busy or not self.current():
            return False
        page = self.page
        if action == "overview":
            operation = lambda: self.broker.overview(identity_id=self.identity, confirmed=True)
        elif action == "capacity":
            operation = lambda: self.broker.capacity(identity_id=self.identity, confirmed=True)
        elif action == "list":
            operation = lambda: self.broker.list_receipts(identity_id=self.identity, confirmed=True)
        elif action == "next" and page is not None and page.has_more:
            operation = lambda: self.broker.next_page(token=page.token, confirmed=True)
        elif (action == "verify" and page is not None and
              any(item is choice for item in page.choices) and choice.parent_relative is not None
              and choice.target_relative is not None):
            operation = lambda: self.broker.verify(token=choice.token, confirmed=True)
        elif action == "simulate" and confirmed is True and self.reviewed is not None:
            token = self.reviewed
            operation = lambda: self.broker.simulate(token=token, confirmed=True)
        else:
            return False
        self.reviewed = None
        if action != "simulate":
            self.choice = choice if action == "verify" else None
        if action in ("list", "next"):
            self.page = None
        self.busy = True
        self.render(self, action, "busy", None)
        delivered = False

        def work():
            if not self.current():
                return None
            value = operation()
            return value if self.current() else None

        def done(value):
            nonlocal delivered
            if delivered:
                return
            delivered = True
            self.busy = False
            if not self.current():
                self.page = self.reviewed = self.choice = None
                return
            if action in ("list", "next"):
                self.page = value if type(value) is DerivedReceiptPage else None
            elif action == "verify" and type(value) is str and len(value) == 64:
                self.reviewed = value
            self.render(self, action, "ready", value)

        try:
            admitted = self.host.submit("Revue des variantes dérivées…", work, done)
        except Exception:
            admitted = False
        if admitted is not True:
            done(None)
        return admitted is True

    def dispose(self):
        self.disposed = True
        self.page = self.reviewed = self.choice = None


class DerivedPlugin:
    def __init__(self):
        self.broker = self.activation = None

    def activate(self, context):
        self.broker = context.broker("library.derived.read")
        self.activation = object()
        return self

    def deactivate(self):
        self.activation = self.broker = None

    def describe(self):
        return MaintenanceToolInfo(
            id="maintenance.library.derived-variants", title="Publications dérivées interrompues",
            description="Journal, capacité et simulation supervisée. Lecture seule.",
            icon_name="system-search-symbolic", scope="identity", danger_level="read-only",
            supports_dry_run=True, cancellable=True,
        )

    def plan(self, context):
        return MaintenancePlan(self.describe().id, "Parcours guidé indisponible.",
                               warnings=("guided_maintenance_required",))

    def run(self, plan, control):
        return MaintenanceResult(False, 0, "Parcours guidé indisponible.")

    def controller(self, host, render):
        marker = self.activation
        if marker is None or self.broker is None:
            raise RuntimeError("derived_review_unavailable")
        return DerivedController(self.broker, host, lambda: marker is self.activation, render)

    def build_guided(self, host):
        from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceView
        from gi.repository import Gtk, Adw
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        group = Adw.PreferencesGroup()
        outer.append(group)
        controls = []
        for title, subtitle, label, action in (
            ("Publications dérivées interrompues", "Revue des reçus du profil actif.", "Examiner…", "overview"),
            ("Capacité du journal des variantes", "Réservations et stock nommé distincts.", "Mesurer…", "capacity"),
            ("Sélection des publications interrompues", "Coordonnées non vérifiées ; aucune reprise.", "Lister…", "list"),
        ):
            row = Adw.ActionRow()
            row.set_title(title)
            row.set_subtitle(subtitle)
            button = Gtk.Button(label=label)
            row.add_suffix(button)
            group.add(row)
            button.connect("clicked", lambda _b, a=action: controller.request(a))
            controls.append(button)
        detail = Gtk.Label(xalign=0, wrap=True, selectable=True)
        outer.append(detail)
        choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.append(choices)
        following = Gtk.Button(label="Page suivante")
        simulation = Gtk.Button(label="Confirmer la simulation sans écriture")
        following.set_sensitive(False)
        simulation.set_sensitive(False)
        outer.append(following)
        outer.append(simulation)

        def render(controller, action, state, value):
            ready = state == "ready"
            for button in controls:
                button.set_sensitive(ready)
            following.set_sensitive(ready and controller.page is not None and controller.page.has_more)
            simulation.set_sensitive(ready and controller.reviewed is not None)
            while choices.get_first_child() is not None:
                choices.remove(choices.get_first_child())
            if controller.page is not None:
                for index, choice in enumerate(controller.page.choices, 1):
                    label = Gtk.Label(xalign=0, wrap=True, selectable=True)
                    label.set_text(f"Reçu {index} : {choice.parent_relative or 'Coordonnées indisponibles'}"
                                   f" → {choice.target_relative or 'Non vérifiées'} ; "
                                   f"{choice.media_format or 'format inconnu'}, "
                                   f"{choice.output_size if choice.output_size is not None else '?'} octets déclarés.")
                    choices.append(label)
                    if choice.parent_relative is not None and choice.target_relative is not None:
                        verify = Gtk.Button(label="Vérifier")
                        verify.set_sensitive(ready)
                        verify.connect("clicked", lambda _b, item=choice:
                                       controller.request("verify", choice=item))
                        choices.append(verify)
            if not ready:
                detail.set_text("Revue en cours…")
            elif action == "overview":
                detail.set_text(overview_text(value))
            elif action == "capacity":
                detail.set_text(capacity_text(value))
            elif action in ("list", "next"):
                detail.set_text(f"{len(controller.page.choices)} reçu(s) ; coordonnées non vérifiées. "
                                "Aucune reprise ni suppression." if controller.page else
                                "Liste indisponible ; aucun constat d’absence.")
            elif action == "verify":
                detail.set_text("Lien physique vérifié. Confirmer séparément la simulation ; "
                                "aucun fichier ni enregistrement modifié." if controller.reviewed else
                                "Preuve insuffisante ou périmée ; aucune reprise.")
            else:
                detail.set_text("Simulation vérifiée ; aucune reprise effectuée."
                                if value == "verified_no_write" else
                                "Simulation refusée ; nouvelle revue nécessaire.")

        controller = self.controller(host, render)
        following.connect("clicked", lambda _b: controller.request("next"))
        simulation.connect("clicked", lambda _b: controller.request("simulate", confirmed=True))
        return GuidedMaintenanceView(outer, controller.dispose)


PLUGIN = DerivedPlugin()
