from cifereca.plugin_api import UiPageContribution, UiActionContribution

class Pages:
    def activate(self, context): return self
    def pages(self):
        definitions = [('dashboard','Accueil','view-dashboard-symbolic','usage'),
                       ('search','Recherche','system-search-symbolic','usage'),
                       ('library','Bibliothèque','accessories-dictionary-symbolic','usage'),
                       ('recent','Récents','document-open-recent-symbolic','usage'),
                       ('loans','Emprunts','folder-documents-symbolic','usage'),
                       ('history','Historique','document-open-recent-symbolic','usage'),
                       ('bookings','Réservations','bookmark-new-symbolic','usage'),
                       ('sync','Synchronisation','emblem-synchronizing-symbolic','configuration'),
                       ('tools','Outils','applications-system-symbolic','configuration'),
                       ('plugins','Plugins','application-x-addon-symbolic','configuration'),
                       ('settings','Paramètres','preferences-system-symbolic','configuration')]
        return [UiPageContribution(route, title, icon, index, lambda host, r=route: host.build_standard_page(r), group,
                (UiActionContribution('import-acsm','Importer un ACSM…',lambda host: host.import_acsm(), 'document-open-symbolic', True),) if route == 'library' else (), 0)
                for index,(route,title,icon,group) in enumerate(definitions)]

class Titlebar:
    def activate(self, context): self.context = context; return self
    def build(self, host):
        from gi.repository import Adw
        return Adw.HeaderBar()
    def selected_actions(self):
        return self.context.configuration().get('actions', [])

class Widgets:
    def activate(self, context): return self
    def describe(self, widget_id):
        return {'id': widget_id} if widget_id == 'query-controls' else None
    def build_query_controls(self, providers, changed, activate, *, library=False):
        from cifereca.gui.query_controls import QueryControls
        return QueryControls(providers, changed, activate, library=library)


class SystemPages:
    def activate(self, context):
        return self

    def pages(self):
        return [UiPageContribution('system-status', 'État du système',
                'dialog-information-symbolic', 900, self.build, 'configuration')]

    @staticmethod
    def build(host):
        from gi.repository import Gtk, Adw
        from cifereca.gui.main_window import fr_date, clear_box
        from cifereca.gui.action_widgets import action_button, AdaptiveActionBar, mirror_sensitivity
        scroll = Gtk.ScrolledWindow()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for name in ('top', 'bottom', 'start', 'end'):
            getattr(outer, 'set_margin_' + name)(20)
        scroll.set_child(outer)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        refresh = action_button('Actualiser', 'view-refresh-symbolic')
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label='État du système', xalign=0); title.set_hexpand(True); title.add_css_class('page-title')
        actions = AdaptiveActionBar(source=header); actions.append(refresh)
        header.append(title); header.append(actions); outer.append(header); outer.append(content)
        footer = AdaptiveActionBar(source=outer); footer.set_halign(Gtk.Align.END)
        refresh_bottom = action_button('Actualiser', 'view-refresh-symbolic'); footer.append(refresh_bottom)
        mirror_sensitivity(refresh, refresh_bottom); outer.append(footer)

        def render(*_):
            clear_box(content)
            data = host.system_status()
            summary = Gtk.Label(label=f"● {data['ready']} prêts · {data['attention']} à examiner", xalign=0)
            summary.add_css_class('warning' if data['attention'] else 'success'); content.append(summary)
            group = Adw.PreferencesGroup(); group.set_title('Installation'); content.append(group)
            row = Adw.ActionRow(); row.set_title('Core · ' + data['version'])
            row.set_subtitle('Installé le ' + fr_date(data['installed_at'], True) if data['installed_at'] else 'Date d’installation inconnue')
            group.add(row)
            states = {'ready':'Prêt', 'disabled':'Désactivé', 'blocked':'Bloqué', 'error':'Erreur', 'incompatible':'Incompatible'}
            for plugin in data['plugins']:
                item = Adw.ExpanderRow()
                item.set_title(f"{plugin['display_name']} · {plugin['version']}")
                state = states.get(plugin['state'], plugin['state']) if plugin['enabled'] else 'Désactivé'
                item.set_subtitle(state + ' · ' + (fr_date(plugin.get('installed_at'), True) if plugin.get('installed_at') else 'Date inconnue'))
                detail = Adw.ActionRow(); detail.set_title(plugin['plugin_id'])
                detail.set_subtitle(f"{plugin['type']} · {plugin['package_id']} {plugin['package_version']}")
                item.add_row(detail)
                from cifereca.plugin_help import plugin_help
                help_row = Adw.ActionRow(); help_row.set_title('À quoi sert ce plugin ?')
                help_row.set_subtitle(plugin_help(plugin)); help_row.set_subtitle_lines(0)
                item.add_row(help_row)
                group.add(item)
        refresh.connect('clicked', render); refresh_bottom.connect('clicked', render); render()
        return scroll


class CacheMaintenance:
    def activate(self, context):
        self.cache = context.broker("cache")
        return self

    def describe(self):
        from cifereca.plugin_api import MaintenanceToolInfo
        return MaintenanceToolInfo(
            id="core.cache.cleanup", title="Nettoyer les caches",
            description="Purge les caches reconstruisibles gérés par le Core, sans supprimer les données utilisateur.",
            icon_name="user-trash-symbolic", scope="global", danger_level="repair",
            supports_dry_run=True, cancellable=True,
        )

    def plan(self, context):
        from cifereca.plugin_api import MaintenancePlan
        rows = tuple(self.cache.namespaces()) if self.cache is not None else ()
        entries = sum(row.entries for row in rows if row.purgeable)
        size = sum(row.size_bytes for row in rows if row.purgeable)
        changes = tuple(f"{row.namespace_id} · {row.entries} entrée(s) · {row.size_bytes} octets" for row in rows if row.purgeable and row.entries)
        return MaintenancePlan(
            tool_id="core.cache.cleanup",
            summary=f"{entries} entrée(s) de cache peuvent être supprimées ({size} octets).",
            changes=changes, facts={"entries": entries, "size_bytes": size, "namespaces": tuple(row.namespace_id for row in rows if row.purgeable)},
        )

    def deactivate(self):
        self.cache = None

    def run(self, plan, control):
        from cifereca.plugin_api import MaintenanceResult
        if control is not None:
            control.check_cancelled(); control.report(current=0, total=1, message="Nettoyage des caches…")
        result = self.cache.purge() if self.cache is not None else None
        if control is not None:
            control.report(current=1, total=1, message="Caches nettoyés")
        if result is None:
            return MaintenanceResult(False, 0, "Broker cache indisponible")
        return MaintenanceResult(
            result.ok, result.entries_removed,
            f"{result.entries_removed} entrée(s) supprimée(s), {result.bytes_freed} octets libérés.",
            result.warnings, {"bytes_freed": result.bytes_freed, "namespaces": result.namespaces},
        )
