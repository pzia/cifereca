from cifereca.plugin_api import UiPageContribution


class SystemPages:
    def __init__(self):
        self.diagnostics = None

    def activate(self, context):
        self.diagnostics = context.broker("diagnostics")
        if self.diagnostics is None:
            raise RuntimeError("broker diagnostics indisponible")
        return self

    def pages(self):
        return [
            UiPageContribution(
                "diagnostics",
                "Diagnostic système (secours)",
                "dialog-information-symbolic",
                900,
                self.build,
                "configuration",
            )
        ]

    def build(self, host):
        from gi.repository import Adw, Gdk, Gtk

        from cifereca.gui.action_widgets import AdaptiveActionBar, action_button, mirror_sensitivity
        from cifereca.gui.diagnostic_presentation import diagnostic_plugin_rows
        from cifereca.core.version_inventory import version_inventory
        from cifereca.gui.main_window import clear_box, fr_date

        scroll = Gtk.ScrolledWindow()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for name in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + name)(20)
        scroll.set_child(outer)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        refresh = action_button("Actualiser", "view-refresh-symbolic")
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="État du système", xalign=0)
        title.set_hexpand(True)
        title.add_css_class("page-title")
        actions = AdaptiveActionBar(source=header)
        filter_drop = Gtk.DropDown.new_from_strings(["À examiner", "Tous"])
        filter_drop.set_tooltip_text("Afficher uniquement les anomalies ou l’inventaire complet")
        actions.append(filter_drop)
        copy_versions = action_button("Copier les versions", "edit-copy-symbolic", tooltip="Copier uniquement les versions Core et plugins affichées")
        actions.append(copy_versions)
        copy = action_button("Copier", "edit-copy-symbolic", tooltip="Copier le rapport de support")
        export = action_button("Exporter", "document-save-symbolic", tooltip="Enregistrer le rapport de support dans un fichier texte")
        actions.append(copy)
        actions.append(export)
        actions.append(refresh)
        header.append(title)
        header.append(actions)
        outer.append(header)
        outer.append(content)
        footer = AdaptiveActionBar(source=outer)
        footer.set_halign(Gtk.Align.END)
        copy_bottom = action_button("Copier", "edit-copy-symbolic", tooltip="Copier le rapport de support")
        export_bottom = action_button("Exporter", "document-save-symbolic", tooltip="Enregistrer le rapport de support dans un fichier texte")
        footer.append(copy_bottom)
        footer.append(export_bottom)
        refresh_bottom = action_button("Actualiser", "view-refresh-symbolic")
        footer.append(refresh_bottom)
        mirror_sensitivity(copy, copy_bottom)
        mirror_sensitivity(export, export_bottom)
        mirror_sensitivity(refresh, refresh_bottom)
        outer.append(footer)

        def open_plugin(plugin_id: str) -> None:
            opener = getattr(host, "open_plugin_settings", None)
            if callable(opener):
                opener(plugin_id)
            else:
                host.navigate("plugins")

        def copy_report(*_args) -> None:
            broker = self.diagnostics
            if broker is None:
                return
            exporter = getattr(broker, "export_support_text", None)
            text = str((exporter() if callable(exporter) else broker.export_text()) or "")
            display = Gdk.Display.get_default()
            if display is None:
                host.toast("Presse-papiers indisponible")
                return
            provider = Gdk.ContentProvider.new_for_value(text)
            self._clipboard_provider = provider
            if display.get_clipboard().set_content(provider):
                host.toast("Diagnostic copié")
            else:
                host.toast("Copie du diagnostic impossible")

        def export_report(*_args) -> None:
            broker = self.diagnostics
            if broker is None:
                return
            exporter = getattr(broker, "export_support_text", None)
            text = str((exporter() if callable(exporter) else broker.export_text()) or "")
            chooser = Gtk.FileChooserNative.new(
                "Exporter le diagnostic",
                None,
                Gtk.FileChooserAction.SAVE,
                "Exporter",
                "Annuler",
            )
            chooser.set_current_name("cifereca-diagnostic.txt")

            def chosen(dialog, response) -> None:
                try:
                    if response != Gtk.ResponseType.ACCEPT:
                        return
                    selected = dialog.get_file()
                    path = selected.get_path() if selected is not None else None
                    if not path:
                        raise RuntimeError("destination locale indisponible")
                    from pathlib import Path
                    Path(path).write_text(text, encoding="utf-8")
                    host.toast("Diagnostic exporté")
                except Exception as exc:
                    host.toast(f"Export impossible : {type(exc).__name__}")
                finally:
                    dialog.destroy()

            chooser.connect("response", chosen)
            chooser.show()

        versions_snapshot = [None]

        def copy_version_snapshot(*_args):
            snapshot = versions_snapshot[0]
            if snapshot is None:
                host.toast("Versions indisponibles : actualiser l’inventaire")
                return
            display = Gdk.Display.get_default()
            if display is None:
                host.toast("Presse-papiers indisponible")
                return
            try:
                provider = Gdk.ContentProvider.new_for_value(snapshot.text)
                self._versions_clipboard_provider = provider
                if display.get_clipboard().set_content(provider):
                    host.toast("Versions copiées")
                else:
                    host.toast("Copie des versions impossible")
            except Exception:
                host.toast("Copie des versions impossible")

        def render(*_):
            versions_snapshot[0] = None
            copy_versions.set_sensitive(False)
            clear_box(content)
            broker = self.diagnostics
            if broker is None:
                raise RuntimeError("broker diagnostics indisponible")
            data = broker.system_status()
            versions = version_inventory(data, broker.inventory())
            versions_snapshot[0] = versions
            copy_versions.set_sensitive(True)
            version_group = Adw.PreferencesGroup()
            version_group.set_title("Versions installées / chargées")
            content.append(version_group)
            core_row = Adw.ActionRow()
            core_row.set_title(f"Core Cifereca · v{versions.core_version}")
            core_row.set_subtitle(
                f"{len(versions.plugins)} plugins dans l’inventaire public"
                + (" · inventaire limité à 300" if versions.limited else "")
            )
            version_group.add(core_row)
            for component in versions.plugins:
                item_row = Adw.ActionRow()
                item_row.set_title(f"{component.name} [{component.plugin_id}]")
                item_row.set_subtitle(component.details)
                item_row.set_subtitle_lines(0)
                version_group.add(item_row)
            attention = int(data.get("attention") or 0)
            summary = Gtk.Label(
                label=f"● {data['ready']} prêts · {attention} à examiner",
                xalign=0,
            )
            summary.add_css_class("warning" if attention else "success")
            content.append(summary)

            attention_only = int(filter_drop.get_selected()) == 0
            rows = diagnostic_plugin_rows(data, attention_only=attention_only)
            group = Adw.PreferencesGroup()
            group.set_title("À examiner" if attention_only else "Composants — anomalies en premier")
            content.append(group)
            if not rows:
                empty = Adw.ActionRow()
                empty.set_title("Aucune anomalie à examiner")
                empty.set_subtitle("Tous les plugins chargés sont hors des états bloqué, erreur ou incompatible.")
                group.add(empty)
            for plugin in rows:
                item = Adw.ExpanderRow()
                item.set_title(f"{plugin['display_name']} · {plugin['state_label']}")
                item.set_subtitle(plugin["summary"])
                if plugin["attention"]:
                    item.add_css_class("warning")
                action = Adw.ActionRow()
                action.set_title("Action")
                action.set_subtitle("Ouvrir le gestionnaire sur ce plugin")
                open_button = Gtk.Button(label="Ouvrir Plugins")
                open_button.set_valign(Gtk.Align.CENTER)
                open_button.connect("clicked", lambda _b, pid=plugin["plugin_id"]: open_plugin(pid))
                action.add_suffix(open_button)
                item.add_row(action)
                detail = Adw.ActionRow()
                detail.set_title("Détails techniques")
                detail.set_subtitle(plugin["technical"] or "Aucun détail technique")
                detail.set_subtitle_lines(0)
                item.add_row(detail)
                group.add(item)

        filter_drop.connect("notify::selected", render)
        copy_versions.connect("clicked", copy_version_snapshot)
        copy.connect("clicked", copy_report)
        copy_bottom.connect("clicked", copy_report)
        export.connect("clicked", export_report)
        export_bottom.connect("clicked", export_report)
        refresh.connect("clicked", render)
        refresh_bottom.connect("clicked", render)
        render()
        return scroll
