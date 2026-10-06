"""Stable application-core composition for cifereca."""

from .runtime import CiferecaCore
from .search import LocalLibrarySearch
from .materialization import MaterializationCoordinator, MaterializationResult

__all__ = ["CiferecaCore", "LocalLibrarySearch", "MaterializationCoordinator", "MaterializationResult"]
