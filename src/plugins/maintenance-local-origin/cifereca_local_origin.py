"""#7092 presentation of the public cache-only batch port."""
from threading import Event
from datetime import datetime, timezone
from unicodedata import category

from cifereca.plugin_api import MaintenanceToolInfo, MaintenancePlan, MaintenanceResult
from cifereca.plugin_api.local_origin_review import LocalOriginBatchPage

_LABELS = {
    "title_related": "Titre voisin",
    "title_concordant_author_unknown": "Titre concordant, auteur inconnu",
    "title_concordant_author_divergent": "Titre concordant, auteur divergent",
    "title_author_concordant": "Titre et auteur concordants",
}


def _displayable_source_coordinate(value, *, limit=512):
    return (type(value) is str and 0 < len(value) <= limit
            and not any(category(ch) in ("Cc", "Cf", "Cs", "Zl", "Zp")
                        for ch in value))


def _c_observation_text(value):
    if type(value) is not int or not 0 <= value <= 253402300799:
        return "Date d'observation C indisponible"
    try:
        return "Observation C (UTC) : " + datetime.fromtimestamp(
            value, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return "Date d'observation C indisponible"


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
            if row.reason == "declared_origin_requires_review":
                lines.append("Non évalué : origine déclarée, non attestée. Activer le filtre pour la revoir.")
            elif row.reason in ("declaration_scope_unverified", "declaration_evidence_unavailable"):
                lines.append("Non évalué : preuve de déclaration indisponible ou ambiguë.")
            else:
                lines.append("Non évalué : budget, chemin ou preuves insuffisantes.")
            continue
        if row.reason == "declared_unverified_comparison":
            lines.append("Origine déclarée par l’utilisateur, non attestée ; déclaration conservée.")
        lines.append(f"{row.known_matches} correspondance(s) connue(s) ; absence non prouvée.")
        for m in row.suggestions:
            lines.append(" · ".join(part for part in (
                m.title, m.author, m.provider_id,
                _LABELS.get(m.comparison, "Comparaison descriptive"),
                "Notice ancienne" if m.stale else "Notice en cache",
                "Référence C : " + m.record_id if _displayable_source_coordinate(m.record_id) else "",
                "Collection C : " + m.docbase if _displayable_source_coordinate(m.docbase) else "",
                "Édition C : " + m.edition_id if _displayable_source_coordinate(m.edition_id) else "",
                "Support C : " + m.medium if _displayable_source_coordinate(m.medium) else "",
                "Format C : " + m.media_format if _displayable_source_coordinate(m.media_format) else "",
                _c_observation_text(m.observed_at),
            ) if part))
        if row.more_matches:
            if row.reason == "declared_unverified_comparison":
                lines.append("Autres correspondances possibles ; comparaison limitée aux notices connues.")
            else:
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

    def request(self, *, restart, include_declared=False):
        if (type(include_declared) is not bool or type(restart) is not bool
                or (not restart and include_declared)
                or self.stop is not None or not self.current()):
            return False
        old = self.page
        if not restart and (old is None or not old.can_continue):
            return False
        event = self.stop = Event()
        self.page = None
        # A new explicit review supersedes the previous lease immediately,
        # even if its worker is queued or never dispatched.
        if restart and old is not None:
            self._cancel_token(old.token)
        self.render(None, "busy")
        delivered = False

        def cancelled():
            return event.is_set() or not self.current()

        def discard(page):
            if type(page) is LocalOriginBatchPage:
                self._cancel_token(page.token)

        def work():
            if cancelled():
                discard(old)
                return None
            try:
                if restart:
                    options = {"identity_id": self.identity, "confirmed": True, "cancelled": cancelled}
                    if include_declared:
                        options["include_declared"] = True
                    result = self.broker.preview(**options)
                else:
                    result = self.broker.next_page(token=old.token, confirmed=True, cancelled=cancelled)
            except Exception:
                # A failing activation adapter must never leave a usable
                # continuation or expose arbitrary exception text.
                discard(old)
                return None
            if cancelled():
                discard(result)
                discard(old)
                return None
            return result

        def done(result):
            nonlocal delivered
            if delivered:
                return
            delivered = True
            self.stop = None
            if cancelled():
                discard(result)
                discard(old)
                if self.current():
                    self.render(None, "ready")
                return
            if (type(result) is LocalOriginBatchPage
                    and all(flag is False for flag in (result.coverage_complete,
                            result.provider_access_performed, result.association_authorized))):
                self.page = result
            else:
                discard(result)
                discard(old)
            self.render(self.page, "ready")

        try:
            admitted = self.host.submit("Comparaison documentaire par lots…", work, done)
        except Exception:
            admitted = False
        if admitted is not True:
            done(None)
        return admitted is True

    def cancel(self):
        if not self.current():
            return False
        if self.stop is not None:
            if self.stop.is_set():
                return False
            self.stop.set()
            self.render(None, "cancelling")
            return True
        if self.page is None:
            return False
        # The last page may still own an active Core continuation while the
        # user is idle. Revoke it immediately, not on a future worker.
        self._cancel_token(self.page.token)
        self.page = None
        self.render(None, "cancelled")
        return True

    def _cancel_token(self, token):
        # Revoke only the exact token of this view, not another page.
        try:
            self.broker.cancel(token=token)
        except Exception:
            pass

    def dispose(self):
        self.disposed = True
        if self.stop is not None:
            self.stop.set()
        if self.page is not None:
            self._cancel_token(self.page.token)
        self.page = None


class OriginPlugin:
    def __init__(self):
        self.broker = self.detail_broker = self.activation = None
        self._detail_controller = None

    def activate(self, context):
        self.broker = context.broker("library.origin.read")
        self.detail_broker = context.broker("library.origin.detail.read")
        if self.detail_broker is not None:
            from cifereca_local_origin_detail import DetailController
            self._detail_controller = DetailController
        self.activation = object()
        return self

    def deactivate(self):
        self.activation = self.broker = self.detail_broker = None
        self._detail_controller = None

    def contribute_book_review(self, host):
        marker = self.activation
        if marker is None or self.detail_broker is None or self._detail_controller is None:
            return lambda: None
        controller = self._detail_controller(
            self.detail_broker, host, lambda: marker is self.activation)
        return controller.mount()

    def describe(self):
        return MaintenanceToolInfo(
            id="maintenance.library.local-origin", title="Suggestions de provenance — par lots",
            description="Comparer les fichiers sans provenance attestée aux notices publiques en cache. Lecture seule.",
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
        include = Gtk.CheckButton(label="Inclure les origines déclarées non attestées")
        include.set_active(False)
        include.set_tooltip_text("Comparaison en cache uniquement. Aucune modification de la déclaration.")
        outer.append(include)
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
            include.set_sensitive(ready)
            start.set_sensitive(ready)
            following.set_sensitive(ready and page is not None and page.can_continue)
            cancel.set_sensitive(state == "busy" or (ready and page is not None))
            detail.set_visible(True)
            detail.set_text({"busy": "Comparaison documentaire en cours…",
                             "cancelling": "Arrêt demandé…",
                             "cancelled": "Revue annulée. Nouvelle consultation explicite nécessaire."}
                            .get(state, display(page)))

        controller = self.controller(host, render)
        # Capture the toggle on the UI thread at the explicit preview click.
        # Continuation always uses the broker cursor, never the live widget.
        start.connect("clicked", lambda _b: controller.request(
            restart=True, include_declared=include.get_active()))
        def filter_changed(_button):
            if controller.stop is not None:
                return
            if controller.page is not None:
                controller._cancel_token(controller.page.token)
                controller.page = None
            following.set_sensitive(False)
            detail.set_visible(False)
        include.connect("toggled", filter_changed)
        following.connect("clicked", lambda _b: controller.request(restart=False))
        cancel.connect("clicked", lambda _b: controller.cancel())
        return GuidedMaintenanceView(outer, controller.dispose)


PLUGIN = OriginPlugin()
