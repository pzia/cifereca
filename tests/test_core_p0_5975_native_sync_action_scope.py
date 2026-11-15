"""P0 #5975/#5833: source-sync GTK leases refuse hostile plugin scalar values.

No GTK runtime, network I/O, provider dispatch or persisted rights are needed.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_action_scope import (
    SyncActionScope, capture_sync_action_scope, sync_action_scope_current,
)


class HostileText(str):
    def strip(self, *args, **kwargs):
        raise AssertionError("plugin-controlled strip()")

    def __eq__(self, other):
        raise AssertionError("plugin-controlled equality")

    def __ne__(self, other):
        raise AssertionError("plugin-controlled inequality")


class HostileGeneration(int):
    def __eq__(self, other):
        raise AssertionError("plugin-controlled generation equality")

    def __ne__(self, other):
        raise AssertionError("plugin-controlled generation inequality")


def window(*, identity="alice", identity_generation=7, preview_generation=11):
    token = object()
    return NS(
        _closed=False, _sync_page_token=token,
        _identity_context_generation=identity_generation,
        _sync_preview_generation=preview_generation,
        service=NS(current_identity=NS(identity_id=identity)),
    ), token


@pytest.mark.parametrize("identity", [
    HostileText("alice"), object(), None, "", "  ",
])
def test_capture_refuses_untrusted_identity_without_dispatch(identity):
    w, token = window(identity=identity)
    assert capture_sync_action_scope(
        w, page_token=token, preview_generation=11,
    ) is None


def test_previous_scope_does_not_compare_plugin_owned_current_identity():
    w, token = window()
    scope = capture_sync_action_scope(w, page_token=token, preview_generation=11)
    assert scope is not None and sync_action_scope_current(w, scope)
    w.service.current_identity = NS(identity_id=HostileText("alice"))
    assert not sync_action_scope_current(w, scope)
    w.service.current_identity = NS(identity_id="alice")
    assert sync_action_scope_current(w, scope)


@pytest.mark.parametrize("generation", [
    HostileGeneration(11), True, "11", object(),
])
def test_capture_refuses_non_native_preview_generation(generation):
    w, token = window()
    assert capture_sync_action_scope(
        w, page_token=token, preview_generation=generation,
    ) is None
    w._sync_preview_generation = generation
    assert capture_sync_action_scope(
        w, page_token=token, preview_generation=11,
    ) is None


@pytest.mark.parametrize("generation", [
    HostileGeneration(7), True, "7", object(),
])
def test_capture_refuses_non_native_identity_generation(generation):
    w, token = window(identity_generation=generation)
    assert capture_sync_action_scope(
        w, page_token=token, preview_generation=11,
    ) is None


@pytest.mark.parametrize("attr,bad", [
    ("_identity_context_generation", HostileGeneration(7)),
    ("_identity_context_generation", True),
    ("_sync_preview_generation", HostileGeneration(11)),
    ("_sync_preview_generation", True),
])
def test_existing_scope_refuses_mutated_generation_without_eq(attr, bad):
    w, token = window()
    scope = capture_sync_action_scope(w, page_token=token, preview_generation=11)
    assert scope is not None
    setattr(w, attr, bad)
    assert not sync_action_scope_current(w, scope)


@pytest.mark.parametrize("scope", [
    SyncActionScope(identity_id=HostileText("alice"), page_token=None),
    SyncActionScope(identity_id=object(), page_token=None),
    SyncActionScope(identity_id="alice", page_token=None,
                    identity_generation=HostileGeneration(7)),
    SyncActionScope(identity_id="alice", page_token=None,
                    preview_generation=HostileGeneration(11)),
])
def test_forged_scope_cannot_invoke_hostile_comparison(scope):
    w, token = window()
    object.__setattr__(scope, "page_token", token)
    assert not sync_action_scope_current(w, scope)


def test_exact_builtins_keep_a_b_a_and_preview_rebuild_veto():
    w, token = window()
    scope = capture_sync_action_scope(w, page_token=token, preview_generation=11)
    assert scope is not None and sync_action_scope_current(w, scope)
    w.service.current_identity = NS(identity_id="bob")
    w._identity_context_generation = 8
    assert not sync_action_scope_current(w, scope)
    w.service.current_identity = NS(identity_id="alice")
    assert not sync_action_scope_current(w, scope)
    w._identity_context_generation = 7
    w._sync_preview_generation = 12
    assert not sync_action_scope_current(w, scope)
    w._sync_preview_generation = 11
    assert sync_action_scope_current(w, scope)
