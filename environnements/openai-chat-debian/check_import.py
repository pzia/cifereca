"""Validate the rootless GTK runtime and Cifereca's declared Python dependencies."""
from __future__ import annotations

from importlib import metadata
import json
import os
from pathlib import Path
import sys
import tomllib

import gi
import httpx
import packaging
from packaging.requirements import Requirement

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Gdk, GLib, Adw


BASE = Path(__file__).resolve().parent
REPO_ROOT = BASE.parents[1]
RUNTIME_SITE = (BASE / "runtime/root/usr/lib/python3/dist-packages").resolve()


def project_dependencies() -> dict[str, dict[str, str]]:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())["project"]
    result: dict[str, dict[str, str]] = {}
    for raw_requirement in project.get("dependencies", []):
        requirement = Requirement(raw_requirement)
        if requirement.marker and not requirement.marker.evaluate():
            continue
        distribution = metadata.distribution(requirement.name)
        version = distribution.version
        if requirement.specifier and version not in requirement.specifier:
            raise RuntimeError(
                f"{requirement.name} {version} does not satisfy {requirement.specifier}"
            )
        location = Path(distribution.locate_file("")).resolve()
        if location != RUNTIME_SITE:
            raise RuntimeError(
                f"{requirement.name} resolved outside rootless runtime: {location}"
            )
        result[requirement.name] = {
            "version": version,
            "requirement": str(requirement.specifier) or "*",
            "location": str(location),
        }
    return result


dependencies = project_dependencies()

print(json.dumps({
    "gtk_import": "OK",
    "python_version": sys.version.split()[0],
    "python_executable": sys.executable,
    "pygobject": gi.__version__,
    "gtk": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}",
    "adw_import": "OK",
    "glib": f"{GLib.MAJOR_VERSION}.{GLib.MINOR_VERSION}.{GLib.MICRO_VERSION}",
    "packaging_import": packaging.__version__,
    "httpx_import": httpx.__version__,
    "project_dependencies": dependencies,
    "runtime_site": str(RUNTIME_SITE),
    "gsk_renderer": os.environ.get("GSK_RENDERER"),
    "display": os.environ.get("DISPLAY"),
}, indent=2))
