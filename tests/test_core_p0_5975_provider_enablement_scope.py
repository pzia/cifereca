"""#5975/#5851: provider enablement stays bound to one exact identity.

All fakes are offline; UI selection is never evidence in the Core worker.
"""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS

from cifereca.core import provider_sessions
from cifereca.core.private_inventory_row_scope import selected_private_offer_hint
from cifereca.services import LibraryService
from tests.test_core_p0_5054_selected_private_offer_hint import _fixture, _row


def _core():
    checks = []
    manager = NS(
        enabled_for_identity=lambda plugin, identity: (
            checks.append((plugin, identity)) or identity == "alice"
        ),
        state=NS(identity_enabled_override=lambda _provider, _identity: None),
    )
    descriptor = NS(id="virtual-parent", instantiation="singleton_public")
    state = NS(enabled=True, state="ready", descriptor=descriptor)

    class Headless:
        _plugin_manager = manager
        _virtual_parent_by_provider = {"future-public": "virtual-parent"}

        @property
        def current_identity(self):
            raise AssertionError("Core provider policy read GTK navigation")

        def _provider_plugin_status(self, _provider):
            return state

    return Headless(), checks


def test_headless_provider_policy_requires_exact_identity_and_no_gtk():
    core, checks = _core()
    enabled = provider_sessions._provider_enabled_for_identity
    assert enabled(core, "future-public", "alice") is True
    assert enabled(core, "future-public", "bob") is False
    assert checks == [("virtual-parent", "alice"), ("virtual-parent", "bob")]
    for invalid in (None, "", False, "alice\n", "alice\r", "alice\x00", "a" * 1025):
        assert enabled(core, "future-public", invalid) is False
    assert checks == [("virtual-parent", "alice"), ("virtual-parent", "bob")]


def test_facade_legacy_no_argument_reads_selection_once_only():
    core, checks = _core()
    selections = []

    class Facade:
        _plugin_manager = core._plugin_manager
        _virtual_parent_by_provider = core._virtual_parent_by_provider
        _provider_plugin_status = core._provider_plugin_status

        @property
        def current_identity(self):
            selections.append("read")
            return NS(identity_id="alice" if len(selections) == 1 else "bob")

    facade = Facade()
    assert LibraryService._provider_enabled_for_identity(facade, "future-public")
    assert not LibraryService._provider_enabled_for_identity(facade, "future-public")
    assert selections == ["read", "read"]
    assert checks == [("virtual-parent", "alice"), ("virtual-parent", "bob")]
    # Already admitted caller scope must not read the navigation selection.
    assert LibraryService._provider_enabled_for_identity(
        facade, "future-public", "alice"
    ) is True
    assert selections == ["read", "read"]


def test_private_offer_enablement_never_infers_another_ui_identity():
    service = _fixture(identity="bob")
    checked = []

    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                raise AssertionError("Core selected private offer read GTK identity")
            return getattr(service, name)

    service._provider_enabled_for_identity = lambda provider, identity: (
        checked.append((provider, identity)) or identity == "alice"
    )
    headless = Headless()
    assert selected_private_offer_hint(headless, _row(), identity_id="alice") is True
    assert checked == [("future-store", "alice")]
    assert selected_private_offer_hint(headless, _row(), identity_id="bob") is False
    assert checked[-1] == ("future-store", "bob")
    before = list(checked)
    assert selected_private_offer_hint(headless, _row(), identity_id=None) is False
    assert checked == before
    assert service.source_calls == []


def test_worker_contains_no_current_identity_lookup():
    source = inspect.getsource(provider_sessions._provider_enabled_for_identity)
    tree = ast.parse(source)
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "current_identity"
        for node in ast.walk(tree)
    )
