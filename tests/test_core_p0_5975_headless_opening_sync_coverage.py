"""#5975: opening Sync readiness is a pinned, headless read-only projection.

The worker never reads GTK navigation; if readiness is unknown, it must NOT
silently suppress the initial loan refresh. No source/provider call or grant.
"""
from __future__ import annotations

import ast
import inspect

import pytest

from cifereca.core.provider_sessions import _opening_sync_covers_visible_accounts


class _Headless:
    def __init__(self):
        self.accounts = {"alice": ["source-a", "source-b"], "bob": ["source-b"]}
        self.enabled = {"source-a": True, "source-b": True}
        self.lookups = []
        self.checked = []

    @property
    def current_identity(self):
        raise AssertionError("opening Sync Core must not inspect navigation")

    def accounts_for_identity(self, identity_id):
        self.lookups.append(identity_id)
        return list(self.accounts.get(identity_id, ()))

    def sync_source_enabled(self, account):
        self.checked.append(account)
        return self.enabled.get(account)


def test_opening_coverage_is_exact_and_headless():
    host = _Headless()
    assert _opening_sync_covers_visible_accounts(
        host, selected_identity_id="alice",
    ) is True
    assert host.lookups == ["alice"]
    assert host.checked == ["source-a", "source-b"]

    host.checked.clear()
    host.enabled["source-b"] = False
    assert _opening_sync_covers_visible_accounts(
        host, selected_identity_id="alice",
    ) is False
    assert host.checked == ["source-a", "source-b"]


@pytest.mark.parametrize("bad", [
    None, "", " ", " alice ", "\x00", "alice\n", "alice\r", False, 0,
    "x" * 257,
])
def test_missing_and_malformed_scope_never_suppresses_forced_refresh(bad):
    host = _Headless()
    assert _opening_sync_covers_visible_accounts(
        host, selected_identity_id=bad,
    ) is False
    assert not host.lookups
    assert not host.checked


def test_other_identity_and_missing_accounts_fail_closed():
    host = _Headless()
    host.accounts["bob"] = []
    assert _opening_sync_covers_visible_accounts(
        host, selected_identity_id="bob",
    ) is False
    assert host.checked == []


@pytest.mark.parametrize("failure", [False, None, "true", 1])
def test_non_affirmative_sync_policy_keeps_forced_refresh(failure):
    host = _Headless()
    host.enabled["source-b"] = failure
    assert _opening_sync_covers_visible_accounts(
        host, selected_identity_id="alice",
    ) is False


def test_policy_read_failure_never_becomes_positive():
    host = _Headless()
    def unavailable(_account):
        raise RuntimeError("private-supplier-data")
    host.sync_source_enabled = unavailable
    assert _opening_sync_covers_visible_accounts(
        host, selected_identity_id="alice",
    ) is False


def test_core_worker_declares_explicit_selection_and_no_gui_getter():
    source = inspect.getsource(_opening_sync_covers_visible_accounts)
    tree = ast.parse(source)
    assert "selected_identity_id" in inspect.signature(
        _opening_sync_covers_visible_accounts
    ).parameters
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "current_identity"
        for node in ast.walk(tree)
    )
