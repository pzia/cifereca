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
        from gi.repository import Adw, Gtk

        from cifereca.gui.action_widgets import AdaptiveActionBar, action_button, mirror_sensitivity
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
        actions.append(refresh)
        header.append(title)
        header.append(actions)
        outer.append(header)
        outer.append(content)
        footer = AdaptiveActionBar(source=outer)
        footer.set_halign(Gtk.Align.END)
        refresh_bottom = action_button("Actualiser", "view-refresh-symbolic")
        footer.append(refresh_bottom)
        mirror_sensitivity(refresh, refresh_bottom)
        outer.append(footer)

        def render(*_):
            clear_box(content)
            broker = self.diagnostics
            if broker is None:
                raise RuntimeError("broker diagnostics indisponible")
            data = broker.system_status()
            summary = Gtk.Label(
                label=f"● {data['ready']} prêts · {data['attention']} à examiner",
                xalign=0,
            )
            summary.add_css_class("warning" if data["attention"] else "success")
            content.append(summary)
            group = Adw.PreferencesGroup()
            group.set_title("Installation")
            content.append(group)
            row = Adw.ActionRow()
            row.set_title("Core · " + data["version"])
            row.set_subtitle(
                "Installé le " + fr_date(data["installed_at"], True)
                if data["installed_at"]
                else "Date d’installation inconnue"
            )
            group.add(row)
            states = {
                "ready": "Prêt",
                "disabled": "Désactivé",
                "blocked": "Bloqué",
                "error": "Erreur",
                "incompatible": "Incompatible",
            }
            for plugin in data["plugins"]:
                item = Adw.ExpanderRow()
                item.set_title(f"{plugin['display_name']} · {plugin['version']}")
                state = states.get(plugin["state"], plugin["state"]) if plugin["enabled"] else "Désactivé"
                item.set_subtitle(
                    state
                    + " · "
                    + (
                        fr_date(plugin.get("installed_at"), True)
                        if plugin.get("installed_at")
                        else "Date inconnue"
                    )
                )
                detail = Adw.ActionRow()
                detail.set_title(plugin["plugin_id"])
                detail.set_subtitle(
                    f"{plugin['type']} · {plugin['package_id']} {plugin['package_version']}"
                )
                item.add_row(detail)
                from cifereca.plugin_help import plugin_help

                help_row = Adw.ActionRow()
                help_row.set_title("À quoi sert ce plugin ?")
                help_row.set_subtitle(plugin_help(plugin))
                help_row.set_subtitle_lines(0)
                item.add_row(help_row)
                group.add(item)

        refresh.connect("clicked", render)
        refresh_bottom.connect("clicked", render)
        render()
        return scroll
