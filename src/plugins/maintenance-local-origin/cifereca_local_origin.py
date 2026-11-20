"""#7092 presentation of the public cache-only batch port."""
from threading import Event

from cifereca.plugin_api import MaintenanceToolInfo, MaintenancePlan, MaintenanceResult
from cifereca.plugin_api.local_origin_review import LocalOriginBatchPage

_LABELS = {
    "title_related": "Titre voisin",
    "title_concordant_author_unknown": "Titre concordant, auteur inconnu",
    "title_concordant_author_divergent": "Titre concordant, auteur divergent",
    "title_author_concordant": "Titre et auteur concordants",
}


def display(page):
    if (type(page) is not LocalOriginBatchPage or any(flag is not False for flag in (
            page.coverage_complete, page.provider_access_performed, page.association_authorized))):
        return "Revue arrêtée ou preuves modifiées. Relancer explicitement."
    lines = [f"Page {page.page} · {len(page.entries)} placement(s) candidat(s)",
             "Notices publiques déjà connues uniquement ; couverture partielle.",
             "Aucune origine, association ou licence créée."]
    for row in page.entries:
        lines.extend(("", row.file))
        if row.status != "compared":
            lines.append("Non évalué : budget, chemin ou preuves insuffisantes.")
            continue
        lines.append(f"{row.known_matches} correspondance(s) connue(s) ; absence non prouvée.")
        for m in row.suggestions:
            lines.append(" · ".join(part for part in (
                m.title, m.author, m.provider_id,
                _LABELS.get(m.comparison, "Comparaison descriptive"),
                "Notice ancienne" if m.stale else "Notice en cache",
            ) if part))
        if row.more_matches:
            lines.append("Autres correspondances : poursuivre depuis la fiche Bibliothèque.")
    if not page.entries:
        lines.append("Aucun candidat enregistré sur cette page ; couverture physique non attestée.")
    if page.has_more and not page.can_continue:
        lines.append("Limite de revue atteinte ; d’autres placements peuvent exister.")
    return "\n".join(lines)


class BatchController:
    def __init__(self, broker, host, active, render):
        self.broker, self.host, self.active, self.render = broker, host, active, render
        self.identity = host.identity_id
        self.page = self.stop = None
        self.disposed = False

    def current(self):
        return (not self.disposed and self.active() and self.host.is_current()
                and self.host.identity_id == self.identity)

    def request(self, *, restart):
        if self.stop is not None or not self.current():
            return False
        old = self.page
        if not restart and (old is None or not old.can_continue):
            return False
        event = self.stop = Event()
        self.page = None
        self.render(None, "busy")
        delivered = False

        def cancelled():
            return event.is_set() or not self.current()

        def work():
            if cancelled():
                return None
            if restart:
                result = self.broker.preview(identity_id=self.identity, confirmed=True, cancelled=cancelled)
            else:
                result = self.broker.next_page(token=old.token, confirmed=True, cancelled=cancelled)
            if cancelled():
                if type(result) is LocalOriginBatchPage:
                    self.broker.cancel(token=result.token)
                return None
            return result

        def done(result):
            nonlocal delivered
            if delivered:
                return
            delivered = True
            self.stop = None
            if not self.current():
                return
            if (not event.is_set() and type(result) is LocalOriginBatchPage
                    and all(flag is False for flag in (result.coverage_complete,
                            result.provider_access_performed, result.association_authorized))):
                self.page = result
            self.render(self.page, "ready")

        try:
            admitted = self.host.submit("Comparaison documentaire par lots…", work, done)
        except Exception:
            admitted = False
        if admitted is not True:
            done(None)
        return admitted is True

    def cancel(self):
        if self.stop is None or not self.current() or self.stop.is_set():
            return False
        self.stop.set()
        self.render(None, "cancelling")
        return True

    def dispose(self):
        self.disposed = True
        if self.stop is not None:
            self.stop.set()
        self.page = None


class OriginPlugin:
    def __init__(self):
        self.broker = self.activation = None

    def activate(self, context):
        self.broker = context.broker("library.origin.read")
        self.activation = object()
        return self

    def deactivate(self):
        self.activation = self.broker = None

    def describe(self):
        return MaintenanceToolInfo(
            id="maintenance.library.local-origin", title="Suggestions de provenance — par lots",
            description="Comparer les fichiers sans origine connue aux notices publiques en cache. Lecture seule.",
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
            raise RuntimeError("origin_review_unavailable")
        return BatchController(self.broker, host, lambda: marker is self.activation, render)

    def build_guided(self, host):
        # Additive guided host contract #7087, resolved only at mount.
        from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceView
        from gi.repository import Gtk, Adw
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        group, row = Adw.PreferencesGroup(), Adw.ActionRow()
        row.set_title(self.describe().title)
        row.set_subtitle(self.describe().description)
        start = Gtk.Button(label="Prévisualiser…")
        row.add_suffix(start)
        group.add(row)
        outer.append(group)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        following, cancel = Gtk.Button(label="Page suivante"), Gtk.Button(label="Annuler")
        following.set_sensitive(False)
        cancel.set_sensitive(False)
        actions.append(following)
        actions.append(cancel)
        outer.append(actions)
        detail = Gtk.Label(xalign=0)
        detail.set_wrap(True)
        detail.set_selectable(True)
        detail.set_visible(False)
        outer.append(detail)

        def render(page, state):
            ready = state == "ready"
            start.set_sensitive(ready)
            following.set_sensitive(ready and page is not None and page.can_continue)
            cancel.set_sensitive(state == "busy")
            detail.set_visible(True)
            detail.set_text({"busy": "Comparaison documentaire en cours…",
                             "cancelling": "Arrêt demandé…"}.get(state, display(page)))

        controller = self.controller(host, render)
        start.connect("clicked", lambda _b: controller.request(restart=True))
        following.connect("clicked", lambda _b: controller.request(restart=False))
        cancel.connect("clicked", lambda _b: controller.cancel())
        return GuidedMaintenanceView(outer, controller.dispose)


PLUGIN = OriginPlugin()
