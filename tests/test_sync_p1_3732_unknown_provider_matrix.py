"""#3732: provider-neutral Sync page acceptance with a fictitious adapter.

No GTK runtime, provider HTTP, Core mutation, per-book grant or local file I/O.
Tests only the CoreUI boundary; Core contract tests belong to Agent A.
"""
from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace as NS

import pytest

from tests.test_core_p0_3416_source_sync_consent_ui import renderer
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def _rig(monkeypatch):
    fake = ModuleType("cifereca.gui.source_sync_consent_controls")
    fake.append_source_sync_consent_controls = renderer()
    monkeypatch.setitem(sys.modules, fake.__name__, fake)

    window, broker, calls, _build, _page = _fixture(sources=[])
    sources = [
        _source(
            source_id="future-exact-one", provider_id="provider.future",
            label="Compte fictif A", identity_id="alice", enabled=True,
            sync_capabilities=("remote_inventory", "entitlements"),
        ),
        _source(
            source_id="future-exact-two", provider_id="provider.future",
            label="Compte fictif B", identity_id="alice", enabled=True,
            sync_capabilities=("remote_inventory", "entitlements"),
        ),
    ]
    grants = {
        sid: {"supported": True, "configured": True, "enabled": True,
              "revision": "rev-one", "valid": True}
        for sid in ("future-exact-one", "future-exact-two")
    }
    reads, writes = [], []
    broker.sources = lambda identity: (
        calls.append(("sources", identity)) or list(sources)
    )
    def read(sid):
        reads.append(sid)
        return dict(grants[sid])
    def write(sid, *, enabled, expected_revision):
        writes.append((sid, enabled, expected_revision))
        grants[sid] = dict(
            grants[sid], enabled=enabled, revision="rev-two",
        )
        return dict(grants[sid])
    broker.source_sync_consent = read
    broker.set_source_sync_consent = write
    # Reaching one of these legacy commands would be a regression: an
    # unknown provider must use only the generic source-level contract.
    for name in (
        "canonical_acquisition_choices", "set_canonical_acquisition_consent",
        "prepare_local_materialization", "audit_local_reconciliation",
        "numilog_materialization_schedule", "bnf_auto_download_enabled",
        "set_bnf_auto_download",
    ):
        setattr(broker, name, lambda *_args, **_kwargs: pytest.fail(
            "legacy provider-specific acquisition UI invoked"
        ))
    calls.clear()
    window._refresh_sync_page()
    return window, broker, sources, grants, reads, writes, calls


@pytest.mark.parametrize("target", [0, 1])
def test_future_provider_has_two_independent_exact_source_consent_switches(
    monkeypatch, target,
):
    window, broker, sources, grants, reads, writes, calls = _rig(monkeypatch)
    groups = window._sync_sources_box.children
    assert len(groups) == 2
    assert reads == ["future-exact-one", "future-exact-two"]
    assert all(len(group.children) == 2 for group in groups)
    assert [group.children[0].title for group in groups] == [
        "Compte fictif A", "Compte fictif B",
    ]
    for group in groups:
        assert group.children[1].title == "Téléchargement automatique de ce compte"
    assert writes == []
    assert window.runner.tasks == []

    switch = groups[target].children[1].suffixes[0]
    switch.toggled(False)
    wanted = sources[target].source_id
    untouched = sources[1-target].source_id
    assert writes == [(wanted, False, "rev-one")]
    assert grants[wanted]["enabled"] is False
    assert grants[untouched]["enabled"] is True
    assert not any(call[0] == "sync-source" for call in calls)
    assert window.runner.tasks == []


def test_future_provider_manual_sync_is_exact_and_does_not_grant_downloads(
    monkeypatch,
):
    window, _broker, sources, grants, reads, writes, calls = _rig(monkeypatch)
    group = window._sync_sources_box.children[1]
    group.children[0].suffixes[1].click()  # Synchroniser cette source seulement
    assert len(window.runner.tasks) == 1
    assert window.runner.tasks[0].work() == "ok"
    assert ("sync-source", sources[1].source_id) in calls
    assert ("sync-source", sources[0].source_id) not in calls
    assert not writes
    assert all(value["enabled"] for value in grants.values())


def test_stale_or_foreign_context_cannot_revoke_another_provider_account(
    monkeypatch,
):
    window, _broker, _sources, grants, reads, writes, calls = _rig(monkeypatch)
    groups = window._sync_sources_box.children
    former_first = groups[0].children[1].suffixes[0]
    former_second = groups[1].children[1].suffixes[0]
    window.service.current_identity = NS(identity_id="bob", label="Bob")
    former_first.toggled(False)
    former_second.toggled(False)
    assert writes == []
    assert all(row["enabled"] for row in grants.values())
    assert window.runner.tasks == []
