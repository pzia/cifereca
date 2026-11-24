from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

from ..config import Config
from ..diagnostics import Diagnostics
from ..plugins import PackageStore, PluginConfigStore, PluginManager, PluginStateStore
from ..services import LibraryService
from ..brokers import build_public_brokers
from ..storage import Storage
from .llm_service import optional_llm_service
from ..task_admission import SharedTaskAdmission
from .notice_c_work_runtime import NoticeWorkRuntime, validate_notice_work_admission
from .notice_c_reparse_execution import PublicReplayRegistry


@dataclass(slots=True)
class CiferecaCore:
    """Composition root for the cifereca 0.9.2 core.

    GTK and CLI share one Config/Storage/PluginManager/LibraryService graph.
    Concrete providers, DRM handlers, transports and storage targets are
    supplied only through Plugin API services.
    """

    config: Config
    storage: Storage
    diagnostics: Diagnostics
    library: LibraryService
    plugins: PluginManager
    brokers: dict[str, object] = field(default_factory=dict, repr=False)
    notice_work: NoticeWorkRuntime | None = field(default=None, repr=False)

    @classmethod
    def open(
        cls,
        *,
        config_path: Path | None = None,
        database_path: Path | None = None,
        diagnostics_enabled: bool | None = None,
        diagnostics_level: str | None = None,
        builtin_ui: bool = True,
        no_plugins: bool = False,
        force_plugins: tuple[str, ...] = (),
        activate_plugins: bool = True,
        notice_work_admission: SharedTaskAdmission | None = None,
        notice_replay_registry: PublicReplayRegistry | None = None,
    ) -> Self:
        # Explicit trusted composition/acceptance option only; ordinary
        # CLI and GTK startup retain their current admission and behavior.
        if notice_replay_registry is not None and (
                type(notice_replay_registry) is not PublicReplayRegistry
                or notice_work_admission is None):
            raise ValueError("notice_reparse_invalid_registry")
        if notice_work_admission is not None:
            validate_notice_work_admission(notice_work_admission)
        config = Config(config_path) if config_path is not None else Config()
        # Normal Search also reviews approved witnesses without the optional
        # work pump; both paths must use the same configured destination.
        capture_directory = None
        configured = config.get("diagnostics.regression_capture_directory", None)
        if configured is not None:
            if type(configured) is not str or not configured.strip() or "\x00" in configured:
                raise ValueError("notice_capture_invalid_directory")
            try:
                capture_directory = Path(configured).expanduser()
            except (ValueError, RuntimeError):
                raise ValueError("notice_capture_invalid_directory") from None
            if not capture_directory.is_absolute():
                raise ValueError("notice_capture_invalid_directory")
        diagnostics = Diagnostics(
            enabled=True if diagnostics_enabled is None else bool(diagnostics_enabled),
            level=str(config.get("diagnostics.level", "normal")) if diagnostics_level is None else diagnostics_level,
        )
        storage = Storage(database_path) if database_path is not None else Storage()
        storage.purge_expired()
        library = LibraryService(config, storage, diagnostics)
        library._notice_regression_capture_directory = capture_directory
        package_store = PackageStore(
            (database_path.parent / "plugins" / "packages") if database_path is not None else None
        )
        state_store = (
            PluginStateStore(config_path.parent / "plugins-state.json")
            if config_path is not None else PluginStateStore()
        )
        plugin_config_store = (
            PluginConfigStore(config_path.parent / "plugins-config.json")
            if config_path is not None else PluginConfigStore()
        )
        brokers = (build_public_brokers(library) if notice_work_admission is None
                   else build_public_brokers(library, task_admission=notice_work_admission))
        # All three tools are optional. Register read factories, not plugin
        # instances, before the manager constructs its activation leases.
        from .maintenance_read_ports import maintenance_read_ports
        brokers.update(maintenance_read_ports(library))
        if builtin_ui:
            # Capability marker only. The actual page mount is the scoped UI
            # host method; no window or execution authority lives in a broker.
            brokers["maintenance.page.host"] = object()
        notice_work = (None if notice_work_admission is None else
                       NoticeWorkRuntime(storage, brokers["tasks"],
                                         replay_registry=notice_replay_registry, diagnostics=diagnostics,
                                         capture_directory=capture_directory))
        if notice_work is not None:
            # Private Core worker port, not a plugin broker.
            library._notice_work_recover_search = notice_work.recover_search_sources
            library._notice_work_search_committed = notice_work.request_tick
        llm = optional_llm_service(config)
        from .notice_descriptive_selection_store import revoke_descriptive_plugin_selections
        plugins = PluginManager(
            builtin_ui=builtin_ui,
            headless=not builtin_ui,
            disable_plugins=no_plugins,
            force_plugins=force_plugins,
            brokers=brokers,
            core_services={"llm": llm} if llm is not None else {},
            package_store=package_store,
            state_store=state_store,
            config_store=plugin_config_store,
            diagnostics=diagnostics,
            before_plugin_retire=lambda ids: revoke_descriptive_plugin_selections(storage, ids),
        )
        if activate_plugins:
            plugins.activate()
        library.attach_plugin_manager(plugins)
        if notice_work is not None and activate_plugins and not no_plugins:
            from .notice_c_purchase_runtime import install_purchase_runtime
            install_purchase_runtime(library, notice_work)
            if notice_replay_registry is None:
                from .notice_c_public_search_replay import qualify_public_search_replay
                from .notice_c_public_search_replay import _anonymous_session

                def register_search_replay(session, source, receipt, identity_id):
                    # The admitted Search producer must still be the exact
                    # registered public singleton, enabled for this profile.
                    provider_id = getattr(session, "provider_id", None)
                    if not _anonymous_session(session, provider_id):
                        return False
                    with library._source_context_lock:
                        key = library._session_key(provider_id, "__public__")
                        if (library._sessions.get(key) is not session
                                or library._provider_enabled_for_identity(
                                    provider_id, identity_id) is not True):
                            return False
                        capability = session.public_search_replay_capability()
                        port = qualify_public_search_replay(session, capability)
                        return notice_work.register_search_replay(source, receipt, port)

                library._notice_work_register_search_replay = register_search_replay
            library._notice_work_request_public_replay = notice_work.request_public_replay
        return cls(
            config=config,
            storage=storage,
            diagnostics=diagnostics,
            library=library,
            plugins=plugins,
            brokers=brokers,
            notice_work=notice_work,
        )

    def configure_notice_consolidator(
        self, payload, *, expected_sha256, canonical_type, scope,
        selected_plugin_id, selected_version, expected_generation,
    ):
        """Trusted lifecycle owner only; explicit independently verified archive.

        Call on the serialized plugin lifecycle owner, not a plugin broker.
        This selects configuration, never schedules execution.
        """
        from .notice_descriptive_active_config import reconcile_active_descriptive_selection
        return reconcile_active_descriptive_selection(
            self.storage, self.plugins, payload, expected_sha256=expected_sha256,
            canonical_type=canonical_type, scope=scope,
            selected_plugin_id=selected_plugin_id, selected_version=selected_version,
            expected_generation=expected_generation,
        )

    @staticmethod
    def abort_directory_migration(*, config_path: Path | None = None, database_path: Path | None = None):
        """Recovery entry point that intentionally does not connect providers."""
        import os
        from ..identities.directory_migration import DirectoryMigration
        from ..locking import InterProcessLock
        from ..paths import acquisition_lock_file
        config = Config(config_path); storage = Storage(database_path)
        root = Path(os.path.expandvars(os.path.expanduser(str(config.get("library.directory", "~/EBooks")))))
        if not root.is_absolute(): root = Path.home() / root
        with InterProcessLock(acquisition_lock_file()):
            return DirectoryMigration(config, storage, root).abort()

    def close(self) -> None:
        if self.notice_work is not None:
            self.notice_work.close()
        self.plugins.close()
        seen: set[int] = set()
        for broker in self.brokers.values():
            marker = id(broker)
            if marker in seen:
                continue
            seen.add(marker)
            close = getattr(broker, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.library.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
