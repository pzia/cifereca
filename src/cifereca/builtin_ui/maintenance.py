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
        snapshots = []
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
            if row.entries:
                namespaces.append(row.namespace_id)
                snapshots.append((
                    row.namespace_id, getattr(row, "owner", ""),
                    getattr(row, "scope", ""), row.entries, row.size_bytes,
                ))
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
                "namespace_snapshots": tuple(snapshots),
            },
        )

    def deactivate(self):
        self.cache = None

    def run(self, plan, control):
        from cifereca.plugin_api import MaintenanceResult

        def finish(ok, removed=0, freed=0, done=(), warnings=()):
            if control is not None:
                if ok:
                    control.report(current=max(1, len(snapshots)),
                                   total=max(1, len(snapshots)),
                                   message="Purge des caches effectuée")
                else:
                    control.report(message="Purge des caches incomplète")
            return MaintenanceResult(
                ok, removed,
                f"{removed} entrée(s) supprimée(s), {freed} octets libérés."
                if ok or removed else "Nettoyage refusé ou incomplet ; relancer la prévisualisation.",
                tuple(warnings),
                {"bytes_freed": freed, "namespaces": tuple(done)},
            )

        if control is not None:
            control.check_cancelled()
        # "repair" tools do not receive the destructive-tools replan lock.
        # The preview itself therefore carries the exact authorized scope.
        facts = getattr(plan, "facts", {})
        snapshots = facts.get("namespace_snapshots") if hasattr(facts, "get") else None
        names = facts.get("namespaces") if hasattr(facts, "get") else None
        valid = (
            getattr(plan, "tool_id", None) == "core.cache.cleanup"
            and isinstance(snapshots, tuple)
            and isinstance(names, tuple)
            and all(
                isinstance(row, tuple) and len(row) == 5
                and type(row[0]) is str and bool(row[0])
                and type(row[1]) is str and bool(row[1])
                and row[2] in ("core", "plugin")
                and type(row[3]) is int and row[3] > 0
                and type(row[4]) is int and row[4] >= 0
                for row in snapshots
            )
        )
        if not valid or names != tuple(row[0] for row in snapshots) or len(set(names)) != len(names):
            snapshots = ()
            return finish(False, warnings=("cache_plan_invalid",))
        if control is not None:
            control.report(current=0, total=max(1, len(snapshots)),
                           message="Nettoyage des caches…")
        if self.cache is None:
            return finish(False, warnings=("cache_unavailable",))
        if not snapshots:
            return finish(True)

        # New namespaces are deliberately ignored; a changed, missing or
        # non-purgeable confirmed namespace invalidates the *whole* preview.
        try:
            current = {row.namespace_id: row for row in self.cache.namespaces()}
        except Exception:
            return finish(False, warnings=("cache_inventory_failed",))
        for expected in snapshots:
            row = current.get(expected[0])
            if row is None or not row.purgeable or (
                row.namespace_id, row.owner, row.scope, row.entries, row.size_bytes
            ) != expected:
                return finish(False, warnings=("cache_plan_stale",))

        removed = freed = 0
        done = []
        warnings = []
        for index, (namespace_id, *_rest) in enumerate(snapshots, start=1):
            # A cancellation after a partial purge retains truthful counters.
            if control is not None and control.cancelled():
                warnings.append("cache_purge_cancelled")
                break
            try:
                result = self.cache.purge(namespace_id)
            except Exception:
                warnings.append("cache_purge_failed")
                break
            if result is None:
                warnings.append("cache_unavailable")
                break
            removed += result.entries_removed
            freed += result.bytes_freed
            done.extend(result.namespaces)
            warnings.extend(result.warnings)
            if not result.ok or namespace_id not in result.namespaces:
                if not result.warnings:
                    warnings.append("cache_purge_incomplete")
                break
            if control is not None and index < len(snapshots):
                control.report(current=index, total=len(snapshots),
                               message="Nettoyage des caches…")
        return finish(not warnings and len(done) == len(snapshots),
                      removed, freed, done, warnings)
