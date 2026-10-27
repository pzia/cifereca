"""Core P0 #3171: a declared presence never exposes OS/provider/SQL secrets.

Cross-provider synthetic fixtures, no provider I/O or acquisition.
"""
from __future__ import annotations

import sqlite3

import pytest

from cifereca.core import library_declared_presence as presence
from cifereca.core.library_fresh_file_check import FreshFileCheck
from cifereca.plugin_api.errors import Cancelled
from tests.test_declared_local_presence import check
from tests.test_library_declared_links import apply, records, rig


PRIVATE = "PRIVATE /home/account/Credentials?token=secret"


def fail(error):
    raise error


@pytest.mark.parametrize("exception_type", [
    sqlite3.OperationalError, OSError, ValueError, KeyError, RuntimeError,
])
def test_internal_failure_has_only_closed_reason(rig, monkeypatch, exception_type):
    service, book, root, _, _ = rig
    apply(rig)
    before = records(service)
    monkeypatch.setattr(
        presence, "configured_identity_roots",
        lambda *_args: fail(exception_type(PRIVATE)),
    )
    result = check(rig)
    assert result.status == "needs_review"
    assert result.reason == "presence_check_unavailable"
    assert PRIVATE not in repr(result)
    assert records(service) == before and book.is_file()


def test_source_guard_exception_is_not_serialized(rig, monkeypatch):
    service, book, root, right, _ = rig
    apply(rig)
    scope = {key: right[key] for key in presence._SCOPE_FIELDS}
    result = presence.verify_declared_presence(
        service, **scope, source_current=lambda: fail(RuntimeError(PRIVATE)),
    )
    assert result.status == "needs_review"
    assert result.reason == "presence_check_unavailable"
    assert PRIVATE not in repr(result)
    assert book.is_file()


def test_file_inspector_cannot_emit_arbitrary_reason(rig, monkeypatch):
    service, book, root, _, _ = rig
    apply(rig)
    monkeypatch.setattr(presence, "acquisition_lock_file", lambda: root.parent / "presence.lock")
    monkeypatch.setattr(
        presence, "revalidate_observed_file",
        lambda *_args, **_kw: FreshFileCheck(False, PRIVATE),
    )
    outcome = check(rig)
    assert outcome.status == "needs_review"
    assert outcome.reason == "file_revalidation_failed"
    assert PRIVATE not in repr(outcome)
    assert book.is_file()


def test_explicit_cancellation_remains_a_cancellation(rig):
    service, book, _, right, _ = rig
    scope = {key: right[key] for key in presence._SCOPE_FIELDS}
    with pytest.raises(Cancelled):
        presence.verify_declared_presence(
            service, **scope, source_current=lambda: True,
            cancelled=lambda: fail(Cancelled("synthetic")),
        )
    assert book.is_file()
