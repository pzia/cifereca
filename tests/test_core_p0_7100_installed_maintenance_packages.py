"""#7100: install actual packages into a private registry, no GTK or business I/O.

The host capability and read ports are witnesses here; the real CoreUI
mount, default installation and GTK/XTEST are separate acceptance gates.
"""
from pathlib import Path
from types import SimpleNamespace as NS
from zipfile import ZipFile

import pytest

from cifereca.core.activation_scoped_broker import ActivationScopedReadFactory
from cifereca.plugin_api.guided_maintenance import GuidedMaintenanceTool
from cifereca.plugins import PackageStore, PluginManager, PluginStateStore

ROOT = Path(__file__).resolve().parents[1] / "src/plugins"
PAGE = ("maintenance-page", "org.cifereca.maintenance.page", "ui.page.maintenance", "maintenance.page.host")
TOOLS = (
    ("maintenance-library-organizer", "org.cifereca.maintenance.library-organizer",
     "maintenance.library.organizer", "library.organizer.read"),
    ("maintenance-local-origin", "org.cifereca.maintenance.local-origin",
     "maintenance.library.local-origin", "library.origin.read"),
    ("maintenance-derived-variants", "org.cifereca.maintenance.derived-variants",
     "maintenance.library.derived-variants", "library.derived.read"),
)


def archive(directory, spec):
    folder, package, _, _ = spec
    target = directory / (package + ".zip")
    with ZipFile(target, "w") as bundle:
        for source in sorted((ROOT / folder).iterdir()):
            if source.is_file() and source.suffix in (".py", ".json", ".md"):
                bundle.write(source, source.name)
    return target


def manager(path, *, host=True, ports=TOOLS, headless=False):
    brokers = {"maintenance.page.host": object()} if host else {}
    for _, _, _, key in ports:
        brokers[key] = ActivationScopedReadFactory(
            lambda: NS(preview=lambda **kw: pytest.fail("mount performed work")), ("preview",),
        )
    if "library.origin.read" in brokers:
        brokers["library.origin.detail.read"] = ActivationScopedReadFactory(
            lambda: NS(), ("preview", "next_page", "search_public", "cancel"))
    return PluginManager(
        package_store=PackageStore(path / "packages"),
        state_store=PluginStateStore(path / "state.json"),
        brokers=brokers, headless=headless,
    )


def routes(runtime):
    return tuple(page.route for page in runtime.page_contributions())


CAPACITY = "maintenance.library.derived-capacity"


def package_tools(spec):
    return {spec[2], CAPACITY} if spec[0] == "maintenance-derived-variants" else {spec[2]}


def tools(runtime):
    return {row.plugin_id for row in runtime.services("maintenance.tool")}


def test_actual_packages_are_independent_and_default_enabled(tmp_path):
    runtime = manager(tmp_path)
    try:
        assert routes(runtime) == () and not tools(runtime)
        runtime.install(archive(tmp_path, PAGE))
        assert routes(runtime) == ("tools",) and not tools(runtime)
        page = runtime.service(PAGE[2])
        ids = set()
        for spec in TOOLS:
            runtime.install(archive(tmp_path, spec))
            ids.update(package_tools(spec))
            assert tools(runtime) == ids and routes(runtime) == ("tools",)
            assert runtime.service(PAGE[2]) is page
            assert isinstance(runtime.service(spec[2]), GuidedMaintenanceTool)
        saved = {key: runtime.service(key) for key in ids}
        runtime.set_enabled(PAGE[2], False)
        assert routes(runtime) == () and tools(runtime) == ids
        assert all(runtime.service(k) is value for k, value in saved.items())
        runtime.set_enabled(PAGE[2], True)
        assert routes(runtime) == ("tools",)
        for spec in TOOLS:
            _, package, key, _ = spec
            runtime.set_enabled(key, False)
            assert routes(runtime) == ("tools",) and tools(runtime) == ids - {key}
            runtime.set_enabled(key, True)
            assert tools(runtime) == ids
            if CAPACITY in package_tools(spec):
                runtime.set_enabled(CAPACITY, False)
                assert tools(runtime) == ids - {CAPACITY}
                runtime.set_enabled(CAPACITY, True)
                assert tools(runtime) == ids
            runtime.remove_package(package)
            ids.difference_update(package_tools(spec))
            assert tools(runtime) == ids and routes(runtime) == ("tools",)
        runtime.remove_package(PAGE[1])
        assert routes(runtime) == () and not tools(runtime)
    finally:
        runtime.close()


@pytest.mark.parametrize("missing", TOOLS)
def test_missing_read_port_only_blocks_its_tool(tmp_path, missing):
    runtime = manager(tmp_path, ports=tuple(spec for spec in TOOLS if spec != missing))
    try:
        for spec in (PAGE, *TOOLS):
            runtime.install(archive(tmp_path, spec))
        assert routes(runtime) == ("tools",)
        assert runtime.status(missing[2]).reason == "missing_broker"
        assert tools(runtime) == set().union(*(package_tools(spec) for spec in TOOLS if spec != missing))
    finally:
        runtime.close()


def test_missing_ui_capability_does_not_disable_independent_tools(tmp_path):
    runtime = manager(tmp_path, host=False)
    try:
        for spec in (PAGE, *TOOLS):
            runtime.install(archive(tmp_path, spec))
        assert routes(runtime) == ()
        assert runtime.status(PAGE[2]).reason == "missing_broker"
        assert tools(runtime) == set().union(*(package_tools(spec) for spec in TOOLS))
    finally:
        runtime.close()
