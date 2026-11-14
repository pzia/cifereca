"""#5975/#5851: Core capability workers refuse navigation owner inference.

Public LibraryService synchronous ABI may snapshot GTK once. A headless Core
worker cannot read current_identity, including for precollected snapshots.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService
from cifereca.core.capability_inventory import capability_matrices_for_identity
from cifereca.core.capability_snapshot_inventory import (
    capability_matrices_with_precollected_evidence,
)
from tests.test_core_p1_2385_catalogue_source_inventory import _rig


class _Headless:
    """Observe any attempted navigation read as a test failure."""

    def __init__(self, service):
        self._service = service

    def __getattr__(self, name):
        if name == "current_identity":
            raise AssertionError("Core attempted mutable navigation owner inference")
        return getattr(self._service, name)


def test_headless_unscoped_workers_fail_closed_without_admission():
    service, calls = _rig()
    headless = _Headless(service)
    assert capability_matrices_for_identity(headless) == ()
    assert capability_matrices_with_precollected_evidence(headless) == ()
    with pytest.raises(ValueError, match="capability_invalid_identity"):
        capability_matrices_with_precollected_evidence(
            headless, sync_sources=(object(),),
        )
    assert calls == []


def test_headless_explicit_identity_projection_ignores_navigation():
    service, calls = _rig()
    headless = _Headless(service)
    alice = capability_matrices_for_identity(headless, identity_id="alice")
    bob = capability_matrices_with_precollected_evidence(
        headless, identity_id="bob",
    )
    assert len(alice) == 8
    assert "kobo" in {row.provider_id for row in alice}
    assert "kobo" not in {row.provider_id for row in bob}
    assert all(row.identity_id == "bob" for matrix in bob
               for row in matrix.observations if row.scope != "unknown")
    assert ("accounts", "alice") in calls
    assert ("accounts", "bob") in calls


def test_sync_facade_snapshots_selected_identity_but_explicit_scope_wins():
    service, _ = _rig()
    alice = LibraryService.capability_matrices(service)
    assert len(alice) == 8
    service.current_identity = NS(identity_id="bob")
    bob = LibraryService.capability_matrices(service)
    assert "kobo" not in {row.provider_id for row in bob}
    assert LibraryService.capability_matrices(service, identity_id="alice") == alice
    assert LibraryService.capability_matrices(service, identity_id="") == ()
    # Precollected projection keeps the same synchronous legacy boundary.
    assert LibraryService.capability_matrices_with_precollected_evidence(
        service, identity_id="alice",
    ) == alice
    assert LibraryService.capability_matrices_with_precollected_evidence(
        service,
    ) == bob
    with pytest.raises(ValueError, match="capability_invalid_identity"):
        LibraryService.capability_matrices_with_precollected_evidence(
            service, identity_id="", sync_sources=(object(),),
        )
