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

    @classmethod
    def open(
        cls,
        *,
        config_path: Path | None = None,
        database_path: Path | None = None,
        diagnostics_enabled: bool | None = None,
        diagnostics_level: str | None = None,
        no_plugins: bool = False,
        force_plugins: tuple[str, ...] = (),
        activate_plugins: bool = True,
    ) -> Self:
        config = Config(config_path) if config_path is not None else Config()
        diagnostics = Diagnostics(
            enabled=True if diagnostics_enabled is None else bool(diagnostics_enabled),
            level=str(config.get("diagnostics.level", "normal")) if diagnostics_level is None else diagnostics_level,
        )
        storage = Storage(database_path) if database_path is not None else Storage()
        storage.purge_expired()
        library = LibraryService(config, storage, diagnostics)
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
        brokers = build_public_brokers(library)
        llm = optional_llm_service(config)
        plugins = PluginManager(
            builtin_ui=True,
            disable_plugins=no_plugins,
            force_plugins=force_plugins,
            brokers=brokers,
            core_services={"llm": llm} if llm is not None else {},
            package_store=package_store,
            state_store=state_store,
            config_store=plugin_config_store,
            diagnostics=diagnostics,
        )
        if activate_plugins:
            plugins.activate()
        library.attach_plugin_manager(plugins)
        return cls(
            config=config,
            storage=storage,
            diagnostics=diagnostics,
            library=library,
            plugins=plugins,
            brokers=brokers,
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
