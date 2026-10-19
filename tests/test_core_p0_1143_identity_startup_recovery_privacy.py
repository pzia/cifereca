"""P0 #1143: startup recovery never emits private directory migration exceptions.

The actual LibraryService recovery method is tested with injected local seams;
no filesystem migration, provider session, SQL write or real identity change.
"""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace as NS
import traceback

import pytest

import cifereca.services as services
from cifereca.providers import ProviderConfigurationError


PRIVATE = "/home/private-person/EBooks/profile/ACSM?token=NEVER_ECHO"


class HostileRecoveryError(OSError):
    def __str__(self):
        raise AssertionError("Private migration errors must never be formatted")
    def __repr__(self):
        raise AssertionError("Private migration errors must never be repr'd")


@pytest.mark.parametrize("error", [
    OSError(PRIVATE), RuntimeError(PRIVATE), HostileRecoveryError(PRIVATE),
])
def test_pending_recovery_failure_exposes_only_constant_startup_message(
    monkeypatch, error,
):
    events = []
    def fail():
        raise error
    facade = NS(
        storage=NS(metadata_get=lambda _key: "pending-local-journal"),
        _identity_directory_engine=lambda: NS(run=fail),
        diagnostics=NS(add=lambda *a, **k: events.append((a, k))),
    )
    monkeypatch.setattr(services, "InterProcessLock", lambda *_a: nullcontext())
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: "/synthetic/lock")
    with pytest.raises(ProviderConfigurationError) as caught:
        services.LibraryService._recover_identity_directory_migration(facade)
    assert str(caught.value) == "Migration de répertoire à reprendre"
    rendered = "".join(traceback.format_exception(caught.value))
    assert "NEVER_ECHO" not in rendered
    assert "private-person" not in rendered
    assert "HostileRecoveryError" not in rendered
    assert events == []


def test_successful_recovery_keeps_original_result_and_diagnostic(
    monkeypatch,
):
    events = []
    original = {"migrated": 1, "units": 2, "collisions": 0}
    facade = NS(
        storage=NS(metadata_get=lambda _key: "pending-local-journal"),
        _identity_directory_engine=lambda: NS(run=lambda: original),
        diagnostics=NS(add=lambda *a, **k: events.append((a, k))),
    )
    monkeypatch.setattr(services, "InterProcessLock", lambda *_a: nullcontext())
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: "/synthetic/lock")
    assert services.LibraryService._recover_identity_directory_migration(facade) is original
    assert events == [(("IDENTITY", "directory migration recovery", "ok"), {
        "detail": original,
    })]


def test_no_pending_journal_does_not_start_a_migration(monkeypatch):
    facade = NS(
        storage=NS(metadata_get=lambda _key: None),
        _identity_directory_engine=lambda: pytest.fail("no migration allowed"),
    )
    assert services.LibraryService._recover_identity_directory_migration(facade) is None
