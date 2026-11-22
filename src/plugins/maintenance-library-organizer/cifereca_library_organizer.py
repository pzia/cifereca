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
    if page.dry_run_state:
        lines.extend(("", "Simulation confirmée — aucune exécution."
                      if page.dry_run_state == "confirmed" else
                      "Aucun renommage non conflictuel observé sur cette page."
                      if page.dry_run_state == "empty" else "Plan à blanc — à confirmer."))
        lines.append("Périmètre simulé : " + (
            "renommages sans collision observée sur cette page (avis)" if page.dry_run_rename_only
            else "toute cette page" if page.dry_run_group is None
            else f"groupe {page.dry_run_group + 1} de cette page"))
        if page.dry_run_rename_only:
            lines.append(f"{len(page.dry_run_entries)} renommage(s) proposé(s) · "
                         f"{page.dry_run_excluded} autre(s) avis non retenu(s). "
                         "Les autres pages ne sont pas vérifiées par cette sélection.")
        labels = {"rename": "Renommage", "classify": "Classement", "keeper": "Copie à conserver"}
        states = {"review_only": "Avis uniquement", "relocation_held": "Déplacement bloqué",
                  "cleanup_held": "Retrait bloqué", "destination_conflict": "Collision"}
        for entry in page.dry_run_entries:
            lines.append(f"{labels.get(entry.operation, 'Revue')} : {entry.current} → {entry.proposed}"
                         f" · {states.get(entry.state, 'Bloqué')}")
    return "\n".join(lines)


def can_prepare(page):
    return (type(page) is OrganizerPageAdvice and page.dry_run_state in ("", "empty")
            and (page.eligible > 0 or any(
                group.copies == 1 and (file.reason == "rename_advice_only" or file.classified_path)
                for group in page.groups for file in group.files)))


class GuidedOrganizer:
    """One worker lease; cancellation revokes pages but never mutates books."""
    def __init__(self, broker, host, active, render):
        self.broker, self.host, self.active, self.render = broker, host, active, render
        self.identity_id = host.identity_id
        self.page = self.stop = self._pending_page = None
        self.busy = self.disposed = self._cancelled = False

    def current(self):
        return (not self.disposed and self.active() and self.host.is_current()
                and self.host.identity_id == self.identity_id)

    def _cancel_token(self, page):
        if type(page) is OrganizerPageAdvice:
            try:
                self.broker.cancel(token=page.token)
            except Exception:
                # Never leak private broker errors to the tool row.
                pass

    def request(self, action, *, group_index=None, rename_only=False):
        if (self.busy or not self.current() or type(rename_only) is not bool
                or (rename_only and (action != "prepare" or group_index is not None))
                or (group_index is not None and (action != "prepare"
                    or type(group_index) is not int or group_index < 0))):
            return False
        page = self.page
        if action == "preview":
            operation = lambda: self.broker.preview(identity_id=self.identity_id, confirmed=True)
        elif action == "next" and page is not None and page.has_more:
            operation = lambda: self.broker.next_page(token=page.token, confirmed=True)
        elif action == "select" and page is not None and page.eligible and not page.selected and not page.dry_run_state:
            operation = lambda: self.broker.select_all_advice(token=page.token, confirmed=True)
        elif action == "prepare" and can_prepare(page) and callable(getattr(self.broker, "prepare_dry_run", None)):
            if group_index is not None and group_index >= len(page.groups):
                return False
            operation = lambda: self.broker.prepare_dry_run(
                token=page.token, confirmed=True,
                **({"group_index": group_index} if group_index is not None else {}),
                **({"rename_only": True} if rename_only else {}))
        elif (action == "confirm" and page is not None and page.dry_run_state == "prepared"
              and callable(getattr(self.broker, "confirm_dry_run", None))):
            operation = lambda: self.broker.confirm_dry_run(token=page.token, confirmed=True)
        else:
            return False

        from threading import Event
        stop = self.stop = Event()
        self._pending_page = page
        self.page = None
        self.busy = True
        self._cancelled = False
        self.render(None, True)
        delivered = False

        def abandoned():
            return stop.is_set() or not self.current()

        def work():
            if abandoned():
                return None
            try:
                value = operation()
            except Exception:
                return None
            if abandoned():
                self._cancel_token(value)
                return None
            return value

        def done(value):
            nonlocal delivered
            if delivered:
                return
            delivered = True
            self.busy = False
            self.stop = self._pending_page = None
            if abandoned():
                self._cancel_token(value)
                self._cancel_token(page)
                if self.current():
                    self.render(None, False)
                return
            if type(value) is OrganizerPageAdvice and value.mutation_authorized is False:
                self.page = value
            else:
                self._cancel_token(value)
                self._cancel_token(page)
            self.render(self.page, False)

        try:
            admitted = self.host.submit("Revue d’organisation locale…", work, done)
        except Exception:
            admitted = False
        if admitted is not True:
            done(None)
        return admitted is True

    def cancel(self):
        if not self.current() or self._cancelled:
            return False
        if self.busy:
            if self.stop is None:
                return False
            self._cancelled = True
            self.stop.set()
            self._cancel_token(self._pending_page)
            self.page = None
            # The worker remains leased; a restart must await its completion.
            self.render(None, True)
            return True
        if self.page is None:
            return False
        self._cancelled = True
        self._cancel_token(self.page)
        self.page = None
        self.render(None, False)
        return True

    def dispose(self):
        self.disposed = True
        if self.stop is not None:
            self.stop.set()
        self._cancel_token(self._pending_page)
        self._cancel_token(self.page)
        self.page = self._pending_page = None


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
        cancel = Gtk.Button(label="Annuler")
        following.set_sensitive(False)
        accept.set_sensitive(False)
        cancel.set_sensitive(False)
        actions.append(following)
        actions.append(accept)
        actions.append(cancel)
        outer.append(actions)
        simulation = Gtk.Button(label="Préparer le plan à blanc")
        simulation.set_sensitive(False)
        simulation_scope = Gtk.ComboBoxText()
        simulation_scope.append("all", "Tous les groupes de cette page")
        simulation_scope.set_active_id("all")
        simulation_scope.set_sensitive(False)
        outer.append(simulation_scope)
        outer.append(simulation)
        detail = Gtk.Label(xalign=0)
        detail.set_wrap(True)
        detail.set_selectable(True)
        detail.set_visible(False)
        outer.append(detail)

        def render(page, busy):
            start.set_sensitive(not busy)
            following.set_sensitive(not busy and page is not None and page.has_more)
            accept.set_sensitive(not busy and page is not None and bool(page.eligible) and not page.selected and not page.dry_run_state)
            prepared = page is not None and page.dry_run_state == "prepared"
            simulation_scope.remove_all()
            simulation_scope.append("all", "Tous les groupes de cette page")
            if page is not None:
                if any(group.copies == 1 and f.reason == "rename_advice_only"
                       for group in page.groups for f in group.files):
                    simulation_scope.append("rename-only", "Renommages sans collision observée (page)")
                for index, group in enumerate(page.groups):
                    if (group.keeper or any(
                            f.reason == "rename_advice_only" or f.classified_path
                            for f in group.files)):
                        simulation_scope.append(str(index), f"Groupe {index + 1}")
            simulation_scope.set_active_id(
                "rename-only" if page is not None and page.dry_run_rename_only
                else str(page.dry_run_group) if page is not None and page.dry_run_group is not None
                else "all")
            simulation_scope.set_sensitive(not busy and can_prepare(page))
            simulation.set_label("Confirmer la simulation" if prepared else "Préparer le plan à blanc")
            simulation.set_sensitive(not busy and ((prepared and callable(getattr(controller.broker, "confirm_dry_run", None)))
                or (can_prepare(page) and callable(getattr(controller.broker, "prepare_dry_run", None)))))
            cancel.set_sensitive(not controller._cancelled and (busy or page is not None))
            detail.set_visible(True)
            detail.set_text(
                "Arrêt demandé…" if busy and controller._cancelled
                else "Revue locale en cours…" if busy
                else "Revue annulée. Relancer explicitement." if controller._cancelled
                else display(page)
            )

        controller = self.controller(host, render)
        start.connect("clicked", lambda _b: controller.request("preview"))
        following.connect("clicked", lambda _b: controller.request("next"))
        accept.connect("clicked", lambda _b: controller.request("select"))
        def simulate(_button):
            if controller.page is not None and controller.page.dry_run_state == "prepared":
                controller.request("confirm")
            else:
                selection = simulation_scope.get_active_id()
                if selection is None:
                    return
                if selection == "rename-only":
                    controller.request("prepare", rename_only=True)
                else:
                    controller.request("prepare", group_index=None if selection == "all" else int(selection))
        simulation.connect("clicked", simulate)
        cancel.connect("clicked", lambda _b: controller.cancel())
        return GuidedMaintenanceView(outer, controller.dispose)


PLUGIN = OrganizerPlugin()
