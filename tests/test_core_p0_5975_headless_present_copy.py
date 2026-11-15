"""P0 #5975: local A→B placement copies remain headless and revocable.

A positive result here is not a loan or provider right. The real SQLite +
filesystem copy, symlink and A→B→A tests remain in #4662.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core import present_placement_copy as worker
from cifereca.services import LibraryService


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class Headless:
    _account_context_epoch = 7
    _source_context_epoch = 9

    def __init__(self):
        self.requester = NS(identity_id="bob")
        self.sender = NS(identity_id="alice")
        self.count = 0
        self.storage = NS(placement_get=self._placement)

    @property
    def current_identity(self):
        pytest.fail("headless placement copy consulted GTK navigation")

    def identity_by_id(self, identity):
        return {"alice": self.sender, "bob": self.requester}.get(identity)

    def _placement(self, identifier):
        return {
            "source": {
                "identity_id": "alice", "artifact_id": "artifact-A",
                "state": "present",
            },
            "target": {"identity_id": "bob", "state": "present"},
        }.get(identifier)


def _kwargs(host, current):
    return dict(
        source_placement_id="source", source_identity_id="alice",
        source_artifact_id="artifact-A", requesting_identity_id="bob",
        expected_account_context_epoch=7, expected_source_context_epoch=9,
        confirmed=True, requester_identity=host.requester,
        scope_current=current,
    )


def test_headless_copy_worker_never_reads_ui_navigation(monkeypatch):
    host = Headless()
    monkeypatch.setattr(worker, "InterProcessLock", lambda _path: FakeLock())
    monkeypatch.setattr(worker, "acquisition_lock_file", lambda: Path("/synthetic/lock"))
    def materialize(_service, identifier, target, _lock):
        host.count += 1
        assert identifier == "source" and target == "bob"
        return {"status": "complete", "target_identity_id": "bob",
                "placement_id": "target"}
    monkeypatch.setattr(worker, "_materialize_to_identity_locked", materialize)
    calls = []
    def current():
        calls.append(True)
        return True
    result = worker.send_present_placement_copy(host, **_kwargs(host, current))
    assert result["status"] == "complete"
    assert result["retry_borrow"] is False
    assert host.count == 1
    assert len(calls) == 3


def test_headless_copy_refuses_missing_or_revoked_scope_before_lock(monkeypatch):
    host = Headless()
    monkeypatch.setattr(worker, "InterProcessLock", lambda _path: pytest.fail("no lock"))
    monkeypatch.setattr(worker, "acquisition_lock_file", lambda: pytest.fail("no path"))
    for callback in (None, lambda: False, lambda: None):
        result = worker.send_present_placement_copy(host, **_kwargs(host, callback))
        assert result["status"] == "blocked"
        assert result["reason_code"] == "local_send_context_revoked"
        assert result["retry_borrow"] is False


def test_headless_copy_no_success_after_a_b_a_post_materialization(monkeypatch):
    host = Headless()
    monkeypatch.setattr(worker, "InterProcessLock", lambda _path: FakeLock())
    monkeypatch.setattr(worker, "acquisition_lock_file", lambda: Path("/synthetic/lock"))
    def materialize(*_args):
        host.count += 1
        host._account_context_epoch += 2  # A→B→A, same requester object.
        return {"status": "complete", "target_identity_id": "bob",
                "placement_id": "target"}
    monkeypatch.setattr(worker, "_materialize_to_identity_locked", materialize)
    def current():
        return host._account_context_epoch == 7
    result = worker.send_present_placement_copy(host, **_kwargs(host, current))
    assert result["status"] == "blocked"
    assert result["delivery"] == "present"
    assert result["reason_code"] == "local_send_context_revoked"
    assert host.count == 1


def test_facade_supplies_revocable_callback_and_exact_selected_object(monkeypatch):
    host = NS(current_identity=NS(identity_id="bob"),
              _account_context_epoch=7, _source_context_epoch=9)
    captured = []
    def fake_worker(_service, **kwargs):
        captured.append(kwargs)
        assert kwargs["requester_identity"] is host.current_identity
        assert kwargs["scope_current"]() is True
        host._account_context_epoch += 2
        assert kwargs["scope_current"]() is False
        return {"status": "blocked"}
    monkeypatch.setattr(worker, "send_present_placement_copy", fake_worker)
    outcome = LibraryService.send_present_placement_copy(
        host, source_placement_id="source",
        source_identity_id="alice", source_artifact_id="artifact-A",
        requesting_identity_id="bob", expected_account_context_epoch=7,
        expected_source_context_epoch=9, confirmed=True,
    )
    assert outcome["status"] == "blocked"
    assert len(captured) == 1


def test_core_worker_has_no_selected_identity_navigation():
    tree = ast.parse(inspect.getsource(worker.send_present_placement_copy))
    assert not any(
        isinstance(node, ast.Attribute) and node.attr == "current_identity"
        for node in ast.walk(tree)
    )


def test_headless_copy_does_not_treat_navigation_epoch_as_authority(monkeypatch):
    host = Headless()
    host._account_context_epoch = 27  # Navigated A→B→A after consent.
    monkeypatch.setattr(worker, "InterProcessLock", lambda _path: FakeLock())
    monkeypatch.setattr(worker, "acquisition_lock_file", lambda: Path("/synthetic/lock"))
    monkeypatch.setattr(worker, "_materialize_to_identity_locked",
                        lambda *_args: {
                            "status": "complete",
                            "target_identity_id": "bob",
                            "placement_id": "target",
                        })
    result = worker.send_present_placement_copy(
        host, **_kwargs(host, lambda: True), admitted_scope=True,
    )
    assert result["status"] == "complete"
    assert result["copy"] == "created"


def test_admitted_facade_never_resolves_mutable_selected_identity(monkeypatch):
    host = Headless()  # current_identity property forbids access.
    observed = []

    def capture(_service, **kwargs):
        observed.append(kwargs)
        assert kwargs["requester_identity"] is host.requester
        assert kwargs["admitted_scope"] is True
        assert kwargs["scope_current"]() is True
        host._account_context_epoch += 2  # Ordinary UI navigation.
        assert kwargs["scope_current"]() is True
        host._source_context_epoch += 1  # Real source binding revocation.
        assert kwargs["scope_current"]() is False
        return {"status": "blocked"}

    monkeypatch.setattr(worker, "send_present_placement_copy", capture)
    result = LibraryService.send_present_placement_copy(
        host, source_placement_id="source",
        source_identity_id="alice", source_artifact_id="artifact-A",
        requesting_identity_id="bob", expected_account_context_epoch=7,
        expected_source_context_epoch=9, confirmed=True,
        admitted_requester_identity=host.requester,
    )
    assert result["status"] == "blocked"
    assert len(observed) == 1


def test_admitted_facade_is_revoked_by_real_identity_registry_rebind(monkeypatch):
    host = Headless()
    checks = []

    def capture(_service, **kwargs):
        current = kwargs["scope_current"]
        assert current() is True
        host.requester = NS(identity_id="bob")  # Same ID, different object.
        assert current() is False
        checks.append(True)
        return {"status": "blocked"}

    monkeypatch.setattr(worker, "send_present_placement_copy", capture)
    old = host.requester
    result = LibraryService.send_present_placement_copy(
        host, source_placement_id="source",
        source_identity_id="alice", source_artifact_id="artifact-A",
        requesting_identity_id="bob", expected_account_context_epoch=7,
        expected_source_context_epoch=9, confirmed=True,
        admitted_requester_identity=old,
    )
    assert result["status"] == "blocked"
    assert checks == [True]


def test_admitted_facade_is_revoked_by_sender_identity_deletion(monkeypatch):
    host = Headless()
    live = {"sender": host.sender}
    host.identity_by_id = lambda name: (
        live["sender"] if name == "alice" else
        host.requester if name == "bob" else None
    )

    def capture(_service, **kwargs):
        assert kwargs["scope_current"]() is True
        live["sender"] = None
        assert kwargs["scope_current"]() is False
        return {"status": "blocked"}

    monkeypatch.setattr(worker, "send_present_placement_copy", capture)
    outcome = LibraryService.send_present_placement_copy(
        host, source_placement_id="source",
        source_identity_id="alice", source_artifact_id="artifact-A",
        requesting_identity_id="bob", expected_account_context_epoch=7,
        expected_source_context_epoch=9, confirmed=True,
        admitted_requester_identity=host.requester,
    )
    assert outcome["status"] == "blocked"



def test_legacy_core_direct_call_retains_independent_epoch_fence(monkeypatch):
    host = Headless()
    host._account_context_epoch = 27
    monkeypatch.setattr(worker, "InterProcessLock", lambda _path: pytest.fail("no lock"))
    monkeypatch.setattr(worker, "acquisition_lock_file", lambda: pytest.fail("no path"))
    outcome = worker.send_present_placement_copy(
        host, **_kwargs(host, lambda: True),
    )
    assert outcome["status"] == "blocked"
    assert outcome["reason_code"] == "local_send_context_revoked"


@pytest.mark.parametrize("scope_flag", [None, "yes", 1])
def test_core_direct_call_refuses_unnative_admission_flag(scope_flag, monkeypatch):
    host = Headless()
    monkeypatch.setattr(worker, "InterProcessLock", lambda _path: pytest.fail("no lock"))
    outcome = worker.send_present_placement_copy(
        host, **_kwargs(host, lambda: True), admitted_scope=scope_flag,
    )
    assert outcome["status"] == "blocked"
