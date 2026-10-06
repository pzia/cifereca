from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Iterator

from .errors import PluginActivationError


@contextmanager
def package_import_path(root: Path) -> Iterator[None]:
    value = str(root)
    sys.path.insert(0, value)
    try:
        yield
    finally:
        try:
            sys.path.remove(value)
        except ValueError:
            pass


def load_entrypoint(root: Path, entrypoint: str) -> object:
    if ":" not in entrypoint:
        raise PluginActivationError(f"invalid plugin entrypoint: {entrypoint!r}")
    module_name, attr = (part.strip() for part in entrypoint.split(":", 1))
    if not module_name or not attr:
        raise PluginActivationError(f"invalid plugin entrypoint: {entrypoint!r}")
    try:
        # A package upgrade/rollback may reuse the same Python module name.
        # Never let importlib hand the runtime a module from the previously
        # active package version.
        for name in [key for key in sys.modules if key == module_name or key.startswith(module_name + ".")]:
            sys.modules.pop(name, None)
        with package_import_path(root):
            module = importlib.import_module(module_name)
            module_file = getattr(module, "__file__", None)
            if not module_file:
                raise PluginActivationError(f"plugin entrypoint module has no package file: {module_name}")
            resolved_root = root.resolve()
            resolved_module = Path(module_file).resolve()
            if resolved_module != resolved_root and resolved_root not in resolved_module.parents:
                raise PluginActivationError(
                    f"plugin entrypoint module is outside its package: {module_name} ({resolved_module})"
                )
            return getattr(module, attr)
    except Exception as exc:
        raise PluginActivationError(f"cannot load plugin entrypoint {entrypoint}: {exc}") from exc
