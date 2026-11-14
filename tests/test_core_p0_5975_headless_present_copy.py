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
