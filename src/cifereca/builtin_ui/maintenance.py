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
        return self._plan_scan(context, None)

    def plan_with_progress(self, context, control):
        """Optional measured preview on the Core-maintained cache list."""
        return self._plan_scan(context, control)

    def _plan_scan(self, context, control):
        from cifereca.plugin_api import MaintenancePlan

        if control is not None:
            control.check_cancelled()
            control.report(message="Inventaire des caches en cours")
        # A single backend call: there is no honest counter while it blocks.
        # Once returned, count only namespaces already observed, without
        # a second scan or maintenance operation.
        rows = tuple(self.cache.namespaces()) if self.cache is not None else ()
        entries = 0
        size = 0
        changes = []
        namespaces = []
        total = len(rows)
        for index, row in enumerate(rows, start=1):
            if control is not None:
                control.check_cancelled()
                if index == 1 or index % 16 == 0 or index == total:
                    control.report(
                        current=index - 1, total=total,
                        message="Vérification des espaces de cache",
                    )
            if not row.purgeable:
                continue
            entries += row.entries
            size += row.size_bytes
            namespaces.append(row.namespace_id)
            if row.entries:
                changes.append(
                    f"{row.namespace_id} · {row.entries} entrée(s) · {row.size_bytes} octets"
                )
        if control is not None:
            control.check_cancelled()
        return MaintenancePlan(
            tool_id="core.cache.cleanup",
            summary=f"{entries} entrée(s) de cache peuvent être supprimées ({size} octets).",
            changes=tuple(changes),
            facts={
                "entries": entries,
                "size_bytes": size,
                "namespaces": tuple(namespaces),
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
            # Progress is evidence of completion, not a substitute for the
            # backend result. A failed/unavailable purge must not publish 1/1
            # or "Caches nettoyés" before the terminal error state.
            if result is not None and result.ok is True:
                control.report(current=1, total=1, message="Purge des caches effectuée")
            else:
                control.report(message="Purge des caches incomplète")
        if result is None:
            return MaintenanceResult(False, 0, "Broker cache indisponible")
        return MaintenanceResult(
            result.ok,
            result.entries_removed,
            f"{result.entries_removed} entrée(s) supprimée(s), {result.bytes_freed} octets libérés.",
            result.warnings,
            {"bytes_freed": result.bytes_freed, "namespaces": result.namespaces},
        )
