"""Presentation only; the public read broker is the sole organizer engine."""
from cifereca.plugin_api import MaintenanceToolInfo, MaintenancePlan, MaintenanceResult
from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceView
from cifereca.plugin_api.library_organizer import OrganizerPageAdvice

_REASONS = {
    "single_registered_placement": "Un seul placement enregistré",
    "physical_and_reference_review_required": "Copies à vérifier",
    "placement_recovery_required": "Placement historique ou indisponible",
    "legacy_source_scope_unbound": "Provenance historique à examiner",
    "catalogue_scope_mismatch": "Notice locale incohérente",
    "placement_or_artifact_unverified": "Placement non vérifié",
    "scope_changed": "Contexte ou preuves modifiés",
    "physical_budget": "Budget de vérification atteint",
    "excluded_or_unverified_path": "Chemin exclu ou non vérifié",
    "format_or_bundle_held": "Format non traité ou bundle indivisible",
    "physical_unverified": "Octets non vérifiés",
    "unsupported_filename_convention": "Suffixe non traité",
    "description_unverified": "Description OPF non concordante",
    "pdf_description_unverified": "Description PDF non attestée",
    "naming_policy_unverified": "Nom proposé non vérifiable",
    "receipt_discriminator_preserve": "Identifiant de réception à conserver",
    "name_unchanged": "Nom conservé",
    "destination_occupied": "Destination occupée",
    "rename_advice_only": "Suggestion de renommage",
    "classified_directory_missing": "Dossier inexistant ; aucune création",
    "classified_layout_advice_only": "Disposition indicative ; références à qualifier",
    "classified_target_occupied": "Destination de classement occupée",
    "classified_target_unverified": "Destination de classement non vérifiée",
    "classified_author_unverified": "Auteur non admissible comme dossier",
    "candidate_not_safe_for_retirement": "Dépendances ou preuves insuffisantes",
    "ambiguous_keeper_preference": "Copies de préférence équivalente",
}


def display(page):
    if type(page) is not OrganizerPageAdvice or page.mutation_authorized is not False:
        return "Revue indisponible ou contexte modifié. Aucun constat d’absence."
    lines = [
        f"Page {page.page} · {page.placement_count} placements SQL · {page.group_count} groupes",
        "Aperçu partiel du nommage et des catégories déclarées dans l’OPF ; aucun classement exécuté.",
        "Les dossiers actuels sont conservés. Aucun déplacement ni retrait.",
        f"{page.eligible} avis keeper présélectionnables sur cette page · {page.selected} sélectionnés",
    ]
    for index, group in enumerate(page.groups, 1):
        lines.extend(("", f"Groupe {index} · {group.copies} copie(s) · " +
                      _REASONS.get(group.reason, "Revue nécessaire")))
        if group.category_evidence == "verified_opf_type_claim":
            lines.append("Catégorie proposée (type déclaré dans l’OPF vérifié) : " + group.category)
        else:
            lines.append("Catégorie proposée : Non classés (preuve insuffisante)")
        for file in group.files:
            lines.append(file.current or "Chemin exclu")
            if file.proposed_name:
                lines.append("  Nom conseillé : " + file.proposed_name)
            lines.append("  " + _REASONS.get(file.reason, "Revue nécessaire"))
            if file.classified_path:
                lines.append("  Chemin de classement indicatif : " + file.classified_path)
            if file.classified_reason in _REASONS:
                lines.append("  " + _REASONS[file.classified_reason])
        if group.keeper:
            lines.append("Copie recommandée à conserver : " + group.keeper)
    if not page.groups:
        lines.append("Aucun groupe enregistré dans cette page de l’inventaire SQL.")
    lines.extend(("", "« Tout accepter (avis) » ne sélectionne que des recommandations, "
                  "sans effet sur les fichiers, droits ou provenances."))
    return "\n".join(lines)


class GuidedOrganizer:
    """Widget-independent, one request at a time, no queued work after disposal."""
    def __init__(self, broker, host, active, render):
        self.broker, self.host, self.active, self.render = broker, host, active, render
        self.identity_id = host.identity_id
        self.page = None
        self.busy = self.disposed = False

    def current(self):
        return (not self.disposed and self.active() and self.host.is_current()
                and self.host.identity_id == self.identity_id)

    def request(self, action):
        if self.busy or not self.current():
            return False
        page = self.page
        if action == "preview":
            operation = lambda: self.broker.preview(identity_id=self.identity_id, confirmed=True)
        elif action == "next" and page is not None and page.has_more:
            operation = lambda: self.broker.next_page(token=page.token, confirmed=True)
        elif action == "select" and page is not None and page.eligible and not page.selected:
            operation = lambda: self.broker.select_all_advice(token=page.token, confirmed=True)
        else:
            return False
        self.busy = True
        self.render(None, True)
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
                self.page = None
                if not self.disposed and self.active():
                    self.render(None, False)
                return
            self.page = (value if type(value) is OrganizerPageAdvice
                         and value.mutation_authorized is False else None)
            self.render(self.page, False)

        try:
            admitted = self.host.submit("Revue d’organisation locale…", work, done)
        except Exception:
            admitted = False
        if admitted is not True:
            done(None)
        return admitted is True

    def dispose(self):
        self.disposed = True
        self.page = None


class OrganizerPlugin:
    def __init__(self):
        self.broker = None
        self.activation = None

    def activate(self, context):
        self.broker = context.broker("library.organizer.read")
        self.activation = object()
        return self

    def deactivate(self):
        self.activation = None
        self.broker = None

    def describe(self):
        return MaintenanceToolInfo(
            id="maintenance.library.organizer", title="Organiser la bibliothèque",
            description="Revue unique du nommage, du classement et des copies. Lecture seule.",
            icon_name="system-search-symbolic", scope="identity", danger_level="read-only",
            supports_dry_run=True, cancellable=True,
        )

    def plan(self, context):
        # Fail closed on hosts without the guided extension; never silently
        # replace pagination with a second, incomplete maintenance workflow.
        return MaintenancePlan(self.describe().id, "Parcours guidé indisponible.",
                               warnings=("guided_maintenance_required",))

    def run(self, plan, control):
        return MaintenanceResult(False, 0, "Parcours guidé indisponible.")

    def controller(self, host, render):
        activation = self.activation
        if activation is None or self.broker is None:
            raise RuntimeError("organizer_unavailable")
        return GuidedOrganizer(self.broker, host,
                               lambda: activation is self.activation, render)

    def build_guided(self, host):
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
        following, accept = Gtk.Button(label="Page suivante"), Gtk.Button(label="Tout accepter (avis)")
        following.set_sensitive(False)
        accept.set_sensitive(False)
        actions.append(following)
        actions.append(accept)
        outer.append(actions)
        detail = Gtk.Label(xalign=0)
        detail.set_wrap(True)
        detail.set_selectable(True)
        detail.set_visible(False)
        outer.append(detail)

        def render(page, busy):
            start.set_sensitive(not busy)
            following.set_sensitive(not busy and page is not None and page.has_more)
            accept.set_sensitive(not busy and page is not None and bool(page.eligible) and not page.selected)
            detail.set_visible(True)
            detail.set_text("Revue locale en cours…" if busy else display(page))

        controller = self.controller(host, render)
        start.connect("clicked", lambda _b: controller.request("preview"))
        following.connect("clicked", lambda _b: controller.request("next"))
        accept.connect("clicked", lambda _b: controller.request("select"))
        return GuidedMaintenanceView(outer, controller.dispose)


PLUGIN = OrganizerPlugin()
