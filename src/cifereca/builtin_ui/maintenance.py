class CacheMaintenance:
    def activate(self, context):
        self.cache = context.broker("cache")
        return self

    def describe(self):
        from cifereca.plugin_api import MaintenanceToolInfo

        return MaintenanceToolInfo(
            id="core.cache.cleanup",
            title="Nettoyer les caches",
            description="Purge les caches reconstruisibles gérés par le Core, sans supprimer les données utilisateur.",
            icon_name="user-trash-symbolic",
            scope="global",
            danger_level="repair",
            supports_dry_run=True,
            cancellable=True,
        )

    def plan(self, context):
        from cifereca.plugin_api import MaintenancePlan

        rows = tuple(self.cache.namespaces()) if self.cache is not None else ()
        entries = sum(row.entries for row in rows if row.purgeable)
        size = sum(row.size_bytes for row in rows if row.purgeable)
        changes = tuple(
            f"{row.namespace_id} · {row.entries} entrée(s) · {row.size_bytes} octets"
            for row in rows
            if row.purgeable and row.entries
        )
        return MaintenancePlan(
            tool_id="core.cache.cleanup",
            summary=f"{entries} entrée(s) de cache peuvent être supprimées ({size} octets).",
            changes=changes,
            facts={
                "entries": entries,
                "size_bytes": size,
                "namespaces": tuple(row.namespace_id for row in rows if row.purgeable),
            },
        )

    def deactivate(self):
        self.cache = None

    def run(self, plan, control):
        from cifereca.plugin_api import MaintenanceResult

        if control is not None:
            control.check_cancelled()
            control.report(current=0, total=1, message="Nettoyage des caches…")
        result = self.cache.purge() if self.cache is not None else None
        if control is not None:
            control.report(current=1, total=1, message="Caches nettoyés")
        if result is None:
            return MaintenanceResult(False, 0, "Broker cache indisponible")
        return MaintenanceResult(
            result.ok,
            result.entries_removed,
            f"{result.entries_removed} entrée(s) supprimée(s), {result.bytes_freed} octets libérés.",
            result.warnings,
            {"bytes_freed": result.bytes_freed, "namespaces": result.namespaces},
        )
