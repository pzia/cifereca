from cifereca.plugin_api import UiPageContribution, UiActionContribution


class Pages:
    def activate(self, context):
        return self

    def pages(self):
        definitions = [
            ("dashboard", "Accueil", "view-dashboard-symbolic", "usage"),
            ("search", "Recherche", "system-search-symbolic", "usage"),
            ("library", "Bibliothèque", "accessories-dictionary-symbolic", "usage"),
            ("recent", "Récents", "document-open-recent-symbolic", "usage"),
            ("loans", "Emprunts", "folder-documents-symbolic", "usage"),
            ("history", "Historique", "document-open-recent-symbolic", "usage"),
            ("bookings", "Réservations", "bookmark-new-symbolic", "usage"),
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
            )
            for index, (route, title, icon, group) in enumerate(definitions)
        ]
