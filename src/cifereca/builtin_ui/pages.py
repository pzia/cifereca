from cifereca.plugin_api import UiPageContribution, UiActionContribution


def _sync_lifecycle(host, _widget, event):
    # Core-owned page: existing local projection, never Sync admission.
    if event.reason in {"show", "context_changed"} and event.is_current():
        host.window._refresh_sync_page()


class Pages:
    def activate(self, context):
        return self

    def pages(self):
        definitions = [
            ("dashboard", "Accueil", "view-dashboard-symbolic", "usage"),
            ("search", "Recherche", "system-search-symbolic", "usage"),
            ("library", "Bibliothèque", "accessories-dictionary-symbolic", "usage"),
            ("readers", "Liseuses", "drive-removable-media-symbolic", "usage"),
            ("recent", "Récents", "document-open-recent-symbolic", "usage"),
            ("loans", "Emprunts", "folder-documents-symbolic", "usage"),
            ("history", "Historique", "document-open-recent-symbolic", "usage"),
            ("sync", "Synchronisation", "emblem-synchronizing-symbolic", "configuration"),
            ("tools", "Outils", "applications-system-symbolic", "configuration"),
            ("plugins", "Plugins", "application-x-addon-symbolic", "configuration"),
            ("settings", "Paramètres", "preferences-system-symbolic", "configuration"),
        ]
        return [
            UiPageContribution(
                route,
                title,
                icon,
                index,
                lambda host, r=route: host.build_standard_page(r),
                group,
                (
                    UiActionContribution(
                        "import-acsm",
                        "Importer un ACSM…",
                        lambda host: host.import_acsm(),
                        "document-open-symbolic",
                        True,
                    ),
                )
                if route == "library"
                else (),
                0,
                lifecycle=_sync_lifecycle if route == "sync" else None,
            )
            for index, (route, title, icon, group) in enumerate(definitions)
        ]
