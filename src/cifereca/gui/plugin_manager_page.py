from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk, Gio, GLib  # noqa: E402

from cifereca.plugins.configuration import defaults_for_schema, schema_scope
from .action_widgets import action_button, AdaptiveActionBar, mirror_sensitivity
from .plugin_view import (
    PLUGIN_VIEW_FILTERS,
    filter_package_plugins,
    package_matches_query,
)
from .plugin_status_labels import (
    activation_label,
    freshness_label,
    runtime_state_label,
    status_needs_attention,
)


_STATE_LABELS = {
    "ready": "Prêt",
    "disabled": "Désactivé",
    "blocked": "Bloqué",
    "degraded": "Dégradé",
    "incompatible": "Incompatible",
    "error": "Erreur",
}


class PluginManagerPage(Gtk.ScrolledWindow):
    """Human-first plugin manager. Technical details stay behind explicit affordances."""

    def __init__(self, window: Any, manager: Any, on_changed: Callable[[], None] | None = None):
        super().__init__()
        self.window = window
        self.manager = manager
        self.on_changed = on_changed or (lambda: None)
        self._view_query = ""
        self._view_filter = "all"
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.outer.set_margin_top(24); self.outer.set_margin_bottom(24)
        self.outer.set_margin_start(24); self.outer.set_margin_end(24)
        self.set_child(self.outer)
        self.refresh()

    def _toast(self, message: str, timeout: int = 4) -> None:
        self.window.toast(message, timeout)

    def refresh(self) -> None:
        child = self.outer.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling(); self.outer.remove(child); child = nxt
        head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3); title_box.set_hexpand(True)
        title = Gtk.Label(label="Plugins", xalign=0); title.add_css_class("page-title"); title_box.append(title)
        subtitle = Gtk.Label(label="Ajoutez des fonctions sans modifier le cœur de cifereca.", xalign=0)
        subtitle.set_wrap(True); subtitle.add_css_class("dim-label"); title_box.append(subtitle); head.append(title_box)
        actions = AdaptiveActionBar(source=head)
        install = action_button("Installer un ZIP…", "document-open-symbolic", suggested=True)
        install.connect("clicked", self._choose_archive); actions.append(install)
        refresh = action_button("Actualiser", "view-refresh-symbolic")
        refresh.connect("clicked", lambda _b: self._reload()); actions.append(refresh)
        head.append(actions)
        self.outer.append(head)

        statuses = self.manager.statuses()
        activated = sum(1 for status in statuses if status.enabled)
        ready = sum(1 for status in statuses if status.enabled and status.state == "ready")
        attention = sum(1 for status in statuses if status_needs_attention(status))
        summary = Gtk.Label(
            label=(
                f"{len(statuses)} plugin(s) · {activated} activé(s) · "
                f"{ready} prêt(s) · {attention} à examiner"
            ),
            xalign=0,
        )
        summary.add_css_class("dim-label"); self.outer.append(summary)

        inventory = self.manager.package_inventory(include_expected=True)

        filters = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.plugin_search = Gtk.SearchEntry()
        self.plugin_search.set_placeholder_text("Rechercher un plugin ou un package…")
        self.plugin_search.set_hexpand(True)
        self.plugin_search.set_text(self._view_query)
        self.plugin_search.connect("search-changed", self._plugin_search_changed)
        filters.append(self.plugin_search)

        filter_labels = [label for _key, label in PLUGIN_VIEW_FILTERS]
        self.plugin_state_filter = Gtk.DropDown.new_from_strings(filter_labels)
        selected_filter = next(
            (index for index, (key, _label) in enumerate(PLUGIN_VIEW_FILTERS) if key == self._view_filter),
            0,
        )
        self.plugin_state_filter.set_selected(selected_filter)
        self.plugin_state_filter.set_tooltip_text("Filtrer l’inventaire affiché sans modifier l’activation")
        self.plugin_state_filter.connect("notify::selected", self._plugin_filter_changed)
        filters.append(self.plugin_state_filter)
        self.outer.append(filters)

        self.inventory_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.outer.append(self.inventory_box)
        self._render_inventory(statuses=statuses, inventory=inventory)

        footer = AdaptiveActionBar(source=self.outer); footer.set_halign(Gtk.Align.END)
        install_bottom = action_button("Installer un ZIP…", "document-open-symbolic", suggested=True)
        install_bottom.connect("clicked", self._choose_archive); footer.append(install_bottom)
        refresh_bottom = action_button("Actualiser", "view-refresh-symbolic")
        refresh_bottom.connect("clicked", lambda _b: self._reload()); footer.append(refresh_bottom)
        mirror_sensitivity(install, install_bottom); mirror_sensitivity(refresh, refresh_bottom)
        self.outer.append(footer)

    def _plugin_search_changed(self, entry: Gtk.SearchEntry) -> None:
        query = entry.get_text()
        if query == self._view_query:
            return
        self._view_query = query
        self._render_inventory()

    def _plugin_filter_changed(self, dropdown: Gtk.DropDown, _pspec=None) -> None:
        index = int(dropdown.get_selected())
        key = PLUGIN_VIEW_FILTERS[index][0] if 0 <= index < len(PLUGIN_VIEW_FILTERS) else "all"
        if key == self._view_filter:
            return
        self._view_filter = key
        self._render_inventory()

    def _render_inventory(self, *, statuses=None, inventory=None) -> None:
        container = self.inventory_box
        child = container.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            container.remove(child)
            child = nxt

        statuses = list(self.manager.statuses() if statuses is None else statuses)
        inventory = list(
            self.manager.package_inventory(include_expected=True)
            if inventory is None else inventory
        )
        installed_packages = [
            package for package in inventory
            if any(status.descriptor.package_id == package["package_id"] for status in statuses)
        ]
        installed_packages.sort(
            key=lambda package: (
                str(package.get("name") or package["package_id"]).casefold(),
                str(package["package_id"]),
            )
        )

        visible_plugins = 0
        visible_packages = 0
        for package in installed_packages:
            package_plugins = [
                status for status in statuses
                if status.descriptor.package_id == package["package_id"]
            ]
            package_plugins = filter_package_plugins(
                package,
                package_plugins,
                query=self._view_query,
                state_filter=self._view_filter,
            )
            package_plugins.sort(
                key=lambda status: (
                    str(status.descriptor.display_name or status.descriptor.id).casefold(),
                    str(status.descriptor.id),
                )
            )
            if not package_plugins:
                continue
            visible_plugins += len(package_plugins)
            visible_packages += 1
            self._append_package(package, package_plugins)

        visible_missing = 0
        if self._view_filter == "all":
            missing_packages = [
                package for package in inventory
                if package.get("freshness_reason") == "not_installed"
                and package_matches_query(package, self._view_query)
            ]
            if missing_packages:
                missing_heading = Gtk.Label(label="Packages attendus absents", xalign=0)
                missing_heading.add_css_class("heading")
                container.append(missing_heading)
                for package in missing_packages:
                    expected = package.get("expected_version") or "?"
                    missing = Gtk.Label(
                        label=f"{package.get('name') or package['package_id']} · attendu v{expected}",
                        xalign=0,
                    )
                    missing.set_wrap(True)
                    missing.add_css_class("warning")
                    container.append(missing)
                    visible_missing += 1

        if self._view_query or self._view_filter != "all":
            count = Gtk.Label(
                label=f"{visible_plugins} plugin(s) affiché(s) dans {visible_packages} package(s)",
                xalign=0,
            )
            count.add_css_class("caption")
            count.add_css_class("dim-label")
            container.prepend(count)

        if visible_plugins == 0 and visible_missing == 0:
            if not statuses and not self._view_query and self._view_filter == "all":
                message = "Aucun plugin installé. Installez un ZIP pour commencer."
            else:
                filter_label = dict(PLUGIN_VIEW_FILTERS).get(self._view_filter, "Tous")
                query = self._view_query.strip()
                if query:
                    message = f"Aucun plugin ne correspond à « {query} » avec le filtre « {filter_label} »."
                else:
                    message = f"Aucun plugin ne correspond au filtre « {filter_label} »."
            empty = Gtk.Label(label=message, xalign=0)
            empty.set_wrap(True)
            empty.add_css_class("dim-label")
            container.append(empty)

    def _append_package(self, package, package_plugins):
        package_id = str(package["package_id"])
        package_name = str(package.get("name") or package_id)
        frame = Gtk.Frame()
        frame.add_css_class("card")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12); box.set_margin_bottom(12)
        box.set_margin_start(14); box.set_margin_end(14)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        heading = Gtk.Label(label=package_name, xalign=0)
        heading.set_hexpand(True); heading.set_wrap(True); heading.add_css_class("heading")
        header.append(heading)
        active = package.get("active")
        expected = package.get("expected_version")
        version_text = f"v{active}" if active else (f"attendu v{expected}" if expected else "version inconnue")
        version = Gtk.Label(label=version_text, xalign=1)
        version.add_css_class("dim-label"); header.append(version)

        previous = package.get("previous")
        if previous or not package.get("builtin"):
            package_menu = Gtk.MenuButton()
            package_menu.set_icon_name("open-menu-symbolic")
            package_menu.set_tooltip_text("Actions du package")
            package_popover = Gtk.Popover()
            package_menu_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            package_menu_box.set_margin_top(8); package_menu_box.set_margin_bottom(8)
            package_menu_box.set_margin_start(8); package_menu_box.set_margin_end(8)
            if previous:
                rollback = action_button(f"Revenir à {previous}", "edit-undo-symbolic")
                rollback.set_tooltip_text(f"Réactiver la version {previous} du package entier")
                rollback.connect("clicked", lambda _b, pid=package_id: self._rollback(pid))
                package_menu_box.append(rollback)
            if not package.get("builtin"):
                plugin_names = tuple(
                    str(status.descriptor.display_name or status.descriptor.id)
                    for status in package_plugins
                )
                remove = action_button(
                    "Désinstaller le package", "edit-delete-symbolic", destructive=True,
                    tooltip="Désinstaller ce package et toutes les contributions listées ci-dessous",
                )
                remove.connect(
                    "clicked",
                    lambda _b, pid=package_id, pname=package_name, names=plugin_names:
                        self._confirm_remove(pid, pname, names),
                )
                package_menu_box.append(remove)
            package_popover.set_child(package_menu_box)
            package_menu.set_popover(package_popover)
            header.append(package_menu)
        box.append(header)

        publisher = str(package.get("publisher") or "")
        package_meta = package_id + (f" · {publisher}" if publisher else "")
        meta = Gtk.Label(label=package_meta, xalign=0)
        meta.set_wrap(True); meta.add_css_class("caption"); meta.add_css_class("dim-label")
        box.append(meta)

        freshness_status = str(package.get("freshness") or "unknown")
        freshness_text = freshness_label(package)
        if freshness_text:
            freshness = Gtk.Label(label=freshness_text, xalign=0)
            freshness.set_wrap(True)
            freshness.add_css_class("dim-label" if freshness_status == "up_to_date" else "warning")
            box.append(freshness)

        contributions = Gtk.Label(
            label=f"{len(package_plugins)} contribution(s) fournie(s) par ce package",
            xalign=0,
        )
        contributions.add_css_class("caption"); contributions.add_css_class("dim-label")
        box.append(contributions)
        for status in package_plugins:
            plugin_frame = Gtk.Frame()
            plugin_frame.set_child(self._plugin_row(status, package))
            box.append(plugin_frame)

        frame.set_child(box)
        target = getattr(self, "inventory_box", self.outer)
        target.append(frame)

    def _plugin_row(self, status: Any, package=None) -> Gtk.Widget:
        from cifereca.plugin_help import plugin_help
        d = status.descriptor
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        box.set_margin_top(9); box.set_margin_bottom(9)
        box.set_margin_start(10); box.set_margin_end(10)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name = Gtk.Label(label=d.display_name or d.id, xalign=0)
        name.set_hexpand(True); name.set_wrap(True); name.add_css_class("heading")
        header.append(name)
        help_button = Gtk.MenuButton()
        help_button.set_icon_name("dialog-question-symbolic")
        help_button.set_tooltip_text(f"Aide : {d.display_name or d.id}")
        popover = Gtk.Popover()
        help_label = Gtk.Label(label=plugin_help(d), xalign=0)
        help_label.set_wrap(True); help_label.set_max_width_chars(64)
        help_label.set_selectable(True)
        help_label.set_margin_top(12); help_label.set_margin_bottom(12)
        help_label.set_margin_start(12); help_label.set_margin_end(12)
        popover.set_child(help_label); help_button.set_popover(popover)
        header.append(help_button); box.append(header)

        axes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        activation = Gtk.Label(label=f"Activation · {activation_label(status)}", xalign=0)
        activation.add_css_class("caption")
        axes.append(activation)
        runtime = Gtk.Label(label=f"État · {runtime_state_label(status)}", xalign=0)
        runtime.add_css_class("caption")
        if status_needs_attention(status):
            runtime.add_css_class("warning")
        axes.append(runtime)
        box.append(axes)

        if d.description:
            summary = Gtk.Label(label=d.description, xalign=0)
            summary.set_wrap(True); summary.set_hexpand(True)
            summary.add_css_class("caption"); summary.add_css_class("dim-label")
            box.append(summary)
        if status_needs_attention(status):
            warning = Gtk.Label(
                label=f"Action requise · {self._friendly_problem(status)}",
                xalign=0,
            )
            warning.set_wrap(True); warning.add_css_class("warning"); box.append(warning)

        primary_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        if d.config_schema:
            cfg = action_button("Configurer…", "preferences-system-symbolic")
            cfg.connect("clicked", lambda _b, pid=d.id: self._configure(pid))
            primary_actions.append(cfg)
        if d.instantiation == "singleton_public":
            identities = Gtk.MenuButton(label="Identités…")
            identities.set_tooltip_text("Activer ce catalogue pour chaque identité")
            identity_popover = Gtk.Popover()
            choices = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            choices.set_margin_top(10); choices.set_margin_bottom(10)
            choices.set_margin_start(10); choices.set_margin_end(10)
            for identity in self.window.service.identities:
                activation = Gtk.CheckButton(label=identity.label)
                activation.set_active(self.manager.enabled_for_identity(d.id, identity.identity_id))
                activation.set_sensitive(bool(status.enabled and status.state == "ready"))
                activation.connect("toggled", lambda button, pid=d.id, iid=identity.identity_id:
                                   self._identity_toggle(pid, iid, button.get_active()))
                choices.append(activation)
            identity_popover.set_child(choices); identities.set_popover(identity_popover)
            primary_actions.append(identities)

        secondary = Gtk.MenuButton()
        secondary.set_icon_name("open-menu-symbolic")
        secondary.set_tooltip_text(f"Actions secondaires : {d.display_name or d.id}")
        secondary_popover = Gtk.Popover()
        secondary_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        secondary_box.set_margin_top(8); secondary_box.set_margin_bottom(8)
        secondary_box.set_margin_start(8); secondary_box.set_margin_end(8)
        diag = action_button("Diagnostic", "dialog-information-symbolic")
        diag.connect("clicked", lambda _b, pid=d.id: self._diagnostic(pid))
        secondary_box.append(diag)
        secondary_popover.set_child(secondary_box); secondary.set_popover(secondary_popover)
        primary_actions.append(secondary)
        box.append(primary_actions)

        settings = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        priority_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        priority_label = Gtk.Label(label="Priorité", xalign=0)
        priority_label.set_tooltip_text(
            "Ordre de préférence de ce plugin parmi les contributions de la même catégorie"
        )
        priority_group.append(priority_label)
        priority = Gtk.SpinButton.new_with_range(-1000, 1000, 1)
        priority.set_value(status.effective_priority)
        priority.set_tooltip_text(
            "Priorité effective : une valeur plus élevée est préférée dans la même catégorie"
        )
        priority.connect("value-changed", lambda sp, pid=d.id: self._priority(pid, int(sp.get_value())))
        priority_group.append(priority); settings.append(priority_group)

        activation_group = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        activation_caption = Gtk.Label(label="Activé", xalign=0)
        activation_group.append(activation_caption)
        toggle = Gtk.Switch(); toggle.set_active(bool(status.enabled)); toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_tooltip_text(f"Activer ou désactiver {d.display_name or d.id}")
        toggle.connect("notify::active", lambda sw, _p, pid=d.id: self._toggle(pid, sw.get_active()))
        activation_group.append(toggle); settings.append(activation_group)
        box.append(settings)
        return box

    @staticmethod
    def _friendly_problem(status: Any) -> str:
        if status.reason in {"dependency_missing", "dependency_disabled", "dependency_version"}:
            return "Ce plugin attend un autre composant. Activez ou installez la dépendance indiquée dans Diagnostic."
        if status.reason == "dependency_cycle":
            return "Les dépendances de ce plugin forment une boucle. Désactivez l’un des composants concernés."
        if status.reason in {"system_requirement", "system_requirement_missing"}:
            return "Un prérequis système manque ou n’a pas la bonne version. Consultez Diagnostic."
        if status.reason == "conflict":
            return "Ce plugin entre en conflit avec un autre plugin actif."
        if status.reason == "configuration":
            return "Ce plugin doit être configuré avant de pouvoir démarrer. Utilisez Configurer…"
        if status.state == "incompatible":
            return "Cette version du plugin n’est pas compatible avec cette version de cifereca."
        return "Ce plugin ne peut pas démarrer actuellement. Consultez Diagnostic pour le détail technique."

    def _identity_toggle(self, plugin_id, identity_id, enabled):
        try:
            self.manager.set_identity_enabled(plugin_id, identity_id, enabled)
        except Exception as exc:
            self.window.show_error(str(exc), "Activation impossible"); return
        self.on_changed()

    def _reload(self) -> None:
        self.manager.activate(); self.refresh(); self.on_changed()

    def _toggle(self, plugin_id: str, enabled: bool) -> None:
        try:
            self.manager.set_enabled(plugin_id, enabled)
        except Exception as exc:
            self.window.show_error(str(exc), "Impossible de modifier le plugin")
        self.refresh(); self.on_changed()

    def _priority(self, plugin_id: str, value: int) -> None:
        try:
            self.manager.set_priority(plugin_id, value)
            self.manager.activate()
        except Exception as exc:
            self.window.show_error(str(exc), "Priorité invalide")
        self.on_changed()

    def _rollback(self, package_id: str) -> None:
        try:
            version = self.manager.rollback(package_id)
        except Exception as exc:
            self.window.show_error(str(exc), "Rollback impossible"); return
        self._toast(f"Version {version} réactivée"); self.refresh(); self.on_changed()

    def _confirm_remove(
        self,
        package_id: str,
        package_name: str = "",
        plugin_names: tuple[str, ...] = (),
    ) -> None:
        display_name = package_name or package_id
        affected = ", ".join(plugin_names) if plugin_names else "toutes ses contributions"
        body = (
            f"Package : {package_id}\n"
            f"Plugins affectés ({len(plugin_names)}): {affected}\n\n"
            "Les données de configuration sont conservées."
        )
        dialog = Adw.MessageDialog.new(
            self.window,
            f"Désinstaller « {display_name} » ?",
            body,
        )
        dialog.add_response("cancel", "Annuler"); dialog.add_response("remove", "Désinstaller")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, response: self._remove(package_id) if response == "remove" else None)
        dialog.present()

    def _remove(self, package_id: str) -> None:
        try:
            self.manager.remove_package(package_id)
        except Exception as exc:
            self.window.show_error(str(exc), "Désinstallation impossible"); return
        self._toast("Package désinstallé"); self.refresh(); self.on_changed()

    def _choose_archive(self, _button: Gtk.Button) -> None:
        existing = getattr(self.window, "_plugin_archive_chooser", None)
        if existing is not None:
            if hasattr(existing, "show"):
                existing.show()
            return
        filt = Gtk.FileFilter(); filt.set_name("Plugin cifereca (*.zip)"); filt.add_pattern("*.zip")
        if hasattr(Gtk, "FileDialog"):
            chooser = Gtk.FileDialog(); chooser.set_title("Installer un plugin")
            filters = Gio.ListStore.new(Gtk.FileFilter); filters.append(filt)
            chooser.set_filters(filters); chooser.set_default_filter(filt)
            self.window._plugin_archive_chooser = chooser
            chooser.open(self.window, None, self._archive_dialog_finished)
        else:
            chooser = Gtk.FileChooserNative.new("Installer un plugin", self.window, Gtk.FileChooserAction.OPEN, "Examiner", "Annuler")
            chooser.add_filter(filt)
            self.window._plugin_archive_chooser = chooser
            chooser.connect("response", self._archive_chosen); chooser.show()

    def _archive_dialog_finished(self, chooser, result):
        try:
            file = chooser.open_finish(result)
        except GLib.Error as exc:
            cancelled = exc.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)
            if hasattr(Gtk, "DialogError"):
                cancelled = cancelled or exc.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED)
            if not cancelled:
                self.window.show_error(str(exc), "Sélection du ZIP impossible")
            return
        finally:
            if getattr(self.window, "_plugin_archive_chooser", None) is chooser:
                self.window._plugin_archive_chooser = None
        self._review_archive_file(file)

    def _archive_chosen(self, chooser: Gtk.FileChooserNative, response: int) -> None:
        file = chooser.get_file() if response == Gtk.ResponseType.ACCEPT else None
        chooser.hide()
        # Release the native model after its response handler has unwound.
        def release():
            if getattr(self.window, "_plugin_archive_chooser", None) is chooser:
                self.window._plugin_archive_chooser = None
            chooser.destroy()
            return False
        GLib.idle_add(release)
        if file is not None:
            self._review_archive_file(file)

    def _review_archive_file(self, file):
        local_path = file.get_path() if file is not None else None
        if not local_path:
            self.window.show_error("Sélectionnez un ZIP local.", "ZIP inaccessible")
            return
        path = Path(local_path)
        if not path.is_file() or path.suffix.casefold() != ".zip":
            self.window.show_error("Sélectionnez un fichier .zip existant.", "ZIP invalide")
            return
        try:
            manifest = self.manager.inspect_archive(path)
        except Exception as exc:
            self.window.show_error(str(exc), "ZIP de plugin invalide"); return
        names = ", ".join(p.display_name or p.id for p in manifest.plugins)
        required = sorted({dep.plugin for p in manifest.plugins for dep in p.requires})
        system = sorted({req.name or req.path or req.type for p in manifest.plugins for req in p.system_requires})
        details = []
        if required: details.append("Dépendances : " + ", ".join(required))
        if system: details.append("Prérequis système : " + ", ".join(system))
        suffix = "\n" + "\n".join(details) if details else ""
        body = f"{manifest.package.name} · v{manifest.package.version}\nÉditeur : {manifest.package.publisher}\n\nPlugins : {names}{suffix}"
        dialog = Adw.MessageDialog.new(self.window, "Installer ce package ?", body)
        dialog.add_response("cancel", "Annuler"); dialog.add_response("install", "Installer")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, r: self._install(path) if r == "install" else None); dialog.present()

    def _install(self, path: Path) -> None:
        try:
            package = self.manager.install(path, activate=True)
        except Exception as exc:
            self.window.show_error(str(exc), "Installation impossible"); return
        self._toast(f"{package.manifest.package.name} installé"); self.refresh(); self.on_changed()

    def _diagnostic(self, plugin_id: str) -> None:
        row = self.manager.diagnostic(plugin_id)[0]
        from ..diagnostics import redact
        body = json.dumps(redact(row), ensure_ascii=False, indent=2)
        dialog = Adw.MessageDialog.new(self.window, row["display_name"], body)
        dialog.add_response("close", "Fermer"); dialog.present()

    def _configure(self, plugin_id: str) -> None:
        status = self.manager.status(plugin_id); schema = status.descriptor.config_schema
        scope = schema_scope(schema)
        dialog = Gtk.Window(transient_for=self.window, modal=True, title=f"Configurer {status.descriptor.display_name or plugin_id}")
        dialog.set_default_size(520, 420)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        outer.set_margin_top(18); outer.set_margin_bottom(18); outer.set_margin_start(18); outer.set_margin_end(18)
        scope_entry = None
        if scope != "plugin":
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); line.append(Gtk.Label(label=f"{scope.capitalize()} ID"))
            scope_entry = Gtk.Entry(); scope_entry.set_hexpand(True)
            ids = self.manager.config.scope_ids(plugin_id, schema)
            if ids: scope_entry.set_text(ids[0])
            line.append(scope_entry); outer.append(line)
        fields: dict[str, tuple[Gtk.Widget, dict[str, Any]]] = {}
        defaults = defaults_for_schema(schema)
        scope_id = scope_entry.get_text().strip() if scope_entry is not None else None
        try:
            current = self.manager.plugin_config(plugin_id, scope_id=scope_id) if scope == "plugin" or scope_id else defaults
        except Exception:
            current = defaults
        for name, spec in (schema.get("properties") or {}).items():
            if not isinstance(spec, dict): continue
            title = str(spec.get("title") or name)
            typ = str(spec.get("type") or "string")
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); label = Gtk.Label(label=title, xalign=0); label.set_size_request(180, -1); line.append(label)
            if name == "actions" and status.descriptor.type == "ui.titlebar":
                from .shell_model import action_registry, validate_action_selection
                widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                checks = {}
                pages = tuple(getattr(self.window, "_pages", {}).values())
                configured = current.get(name, [])
                configured = configured if isinstance(configured, list) else []
                valid, obsolete = validate_action_selection(pages, configured)
                selected = set(valid)
                for entry in action_registry(pages):
                    scope = "Globale" if entry.scope == "global" else f"Page · {entry.page_title}"
                    check = Gtk.CheckButton(label=f"{entry.title} — {scope}")
                    check.set_tooltip_text(entry.key)
                    check.set_active(entry.key in selected)
                    widget.append(check)
                    checks[entry.key] = check
                for key in obsolete:
                    warning = Gtk.Label(label=f"Action obsolète : {key}", xalign=0)
                    warning.set_wrap(True)
                    warning.add_css_class("warning")
                    widget.append(warning)
                spec = dict(spec)
                spec["_action_checks"] = checks
                spec["_obsolete_actions"] = obsolete
            elif typ == "boolean":
                widget: Gtk.Widget = Gtk.Switch(); widget.set_active(bool(current.get(name, spec.get("default", False))))
            elif typ in {"integer", "number"}:
                lo=float(spec.get("minimum", -100000)); hi=float(spec.get("maximum", 100000)); step=1 if typ=="integer" else .1
                widget=Gtk.SpinButton.new_with_range(lo, hi, step); widget.set_value(float(current.get(name, spec.get("default", 0)) or 0))
            elif isinstance(spec.get("enum"), list):
                values=[str(x) for x in spec["enum"]]; widget=Gtk.DropDown.new_from_strings(values)
                try: widget.set_selected(values.index(str(current.get(name, values[0] if values else ""))))
                except ValueError: pass
                spec = dict(spec); spec["_enum_values"] = values
            else:
                widget=Gtk.Entry(); widget.set_hexpand(True)
                value=current.get(name, spec.get("default", ""))
                if typ in {"array", "object"}: value=json.dumps(value, ensure_ascii=False)
                widget.set_text(str(value if value is not None else ""))
                if bool(spec.get("x-cifereca-secret")): widget.set_visibility(False)
            line.append(widget); outer.append(line); fields[str(name)] = (widget, spec)
        if scope_entry is not None:
            def load_scope(_entry):
                sid = scope_entry.get_text().strip()
                try: values = self.manager.plugin_config(plugin_id, scope_id=sid) if sid else defaults
                except Exception: values = defaults
                for name, (widget, spec) in fields.items():
                    value = values.get(name, spec.get("default"))
                    if isinstance(widget, Gtk.Switch): widget.set_active(bool(value))
                    elif isinstance(widget, Gtk.SpinButton): widget.set_value(float(value or 0))
                    elif isinstance(widget, Gtk.DropDown):
                        options = spec.get("_enum_values", [])
                        widget.set_selected(options.index(str(value)) if str(value) in options else 0)
                    elif isinstance(widget, Gtk.Entry):
                        widget.set_text(json.dumps(value, ensure_ascii=False) if spec.get("type") in {"array", "object"} else str(value or ""))
            scope_entry.connect("changed", load_scope)
        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); buttons.set_halign(Gtk.Align.END)
        cancel=Gtk.Button(label="Annuler"); cancel.connect("clicked", lambda _b: dialog.close()); buttons.append(cancel)
        save=Gtk.Button(label="Enregistrer"); save.add_css_class("suggested-action"); buttons.append(save); outer.append(buttons)
        def do_save(_button):
            sid = scope_entry.get_text().strip() if scope_entry is not None else None
            values: dict[str, Any] = {}
            try:
                for name, (widget, spec) in fields.items():
                    typ=str(spec.get("type") or "string")
                    if "_action_checks" in spec:
                        values[name] = [key for key, check in spec["_action_checks"].items() if check.get_active()]
                    elif isinstance(widget, Gtk.Switch): values[name]=widget.get_active()
                    elif isinstance(widget, Gtk.SpinButton): values[name]=int(widget.get_value()) if typ=="integer" else float(widget.get_value())
                    elif isinstance(widget, Gtk.DropDown):
                        enum=spec.get("_enum_values", []); idx=widget.get_selected(); values[name]=enum[idx] if idx < len(enum) else ""
                    else:
                        text=widget.get_text()
                        values[name]=(json.loads(text) if text else ([] if typ=="array" else {})) if typ in {"array","object"} else text
                self.manager.set_plugin_config(plugin_id, values, scope_id=sid)
            except Exception as exc:
                self.window.show_error(str(exc), "Configuration invalide"); return
            dialog.close(); self._toast("Configuration enregistrée"); self.refresh(); self.on_changed()
        save.connect("clicked", do_save)
        scroll = Gtk.ScrolledWindow(); scroll.set_child(outer)
        dialog.set_child(scroll); dialog.present()
