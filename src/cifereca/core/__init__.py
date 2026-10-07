"""Stable application-core composition for cifereca.

The package initializer intentionally avoids importing runtime/plugin composition.
Public symbols are loaded lazily so lightweight submodules such as core.search
remain safe to import from the public plugin API during its own initialization.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from .materialization import MaterializationCoordinator, MaterializationResult
    from .runtime import CiferecaCore
    from .search import LocalLibrarySearch


__all__ = [
    "CiferecaCore",
    "LocalLibrarySearch",
    "MaterializationCoordinator",
    "MaterializationResult",
]


def __getattr__(name: str) -> Any:
    if name == "CiferecaCore":
        from .runtime import CiferecaCore

        return CiferecaCore
    if name == "LocalLibrarySearch":
        from .search import LocalLibrarySearch

        return LocalLibrarySearch
    if name in {"MaterializationCoordinator", "MaterializationResult"}:
        from .materialization import MaterializationCoordinator, MaterializationResult

        return {
            "MaterializationCoordinator": MaterializationCoordinator,
            "MaterializationResult": MaterializationResult,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
