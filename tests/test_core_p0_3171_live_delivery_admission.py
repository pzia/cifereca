"""#3171: the Core's live source consent can reach an opted-in provider GET.

Synthetic v2 candidate and real SQLite/Sync. No network or provider credentials.
Legacy v2 remains callable unchanged until a plugin adopts this extension.
"""
from __future__ import annotations

import pytest

from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3719_source_sync_consent import source, change


def test_opted_in_candidate_receives_live_core_source_admission(source):
    service, db, root, calls, broker = source
    enabled = change(source, True)
    original = service.session.downloadable_selected_delivery_candidate
    admission_results = []

    def guarded(account, *, admission_current, **kwargs):
        assert callable(admission_current)
        # This is a Core-owned, non-serializable callback, not a plugin grant.
        admission_results.extend([admission_current(), admission_current()])
        return original(account, **kwargs)

    service.session.downloadable_selected_delivery_candidate_guarded = guarded
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert admission_results == [True, True]
    assert calls == ["offer", "delivery"]
    assert len(db.placements()) == 1
    assert change(source, False, enabled["revision"])["enabled"] is False


@pytest.mark.parametrize("reenable", [False, True])
def test_consent_revoked_inside_candidate_stream_never_publishes(source, reenable):
    service, db, root, calls, broker = source
    enabled = change(source, True)
    original = service.session.downloadable_selected_delivery_candidate
    observations = []

    def guarded(account, *, admission_current, **kwargs):
        # Model a public transport checking admission on consecutive chunks.
        observations.append(admission_current())
        off = change(source, False, enabled["revision"])
        observations.append(admission_current())
        if reenable:
            new = change(source, True, off["revision"])
            assert new["revision"] != enabled["revision"]
            observations.append(admission_current())
        # Even if a noncompliant producer keeps writing after revocation,
        # Core must refuse the receipt and clean its private staging.
        return original(account, **kwargs)

    service.session.downloadable_selected_delivery_candidate_guarded = guarded
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert observations == ([True, False, False] if reenable else [True, False])
    assert db.placements() == []
    assert list((root / "A" / ".canonical-delivery").glob("run-*")) == []
    assert calls == ["offer", "delivery"]


def test_guarded_candidate_failure_has_no_fallback_to_v2(source):
    service, db, root, calls, broker = source
    change(source, True)

    def guarded(_account, *, admission_current, **_kwargs):
        assert admission_current()
        raise ValueError("synthetic_guarded_transport_failure")

    service.session.downloadable_selected_delivery_candidate_guarded = guarded
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert calls == ["offer"]
    assert db.placements() == []
    assert list((root / "A" / ".canonical-delivery").glob("run-*")) == []


def test_unconfigured_source_never_calls_guarded_candidate(source):
    service, db, root, calls, broker = source
    service.session.downloadable_selected_delivery_candidate_guarded = (
        lambda *_args, **_kwargs: pytest.fail("no Core acquisition admission")
    )
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 0
    assert calls == [] and db.placements() == []


def test_explicit_v2_legacy_candidate_remains_unchanged(source):
    service, db, root, calls, broker = source
    change(source, True)
    # No opt-in method: the unchanged v2 method receives no extra kwargs.
    result = synchronize_account_source(service, service.account)
    assert result["canonical_execution"]["downloaded"] == 1
    assert calls == ["offer", "delivery"]
    assert len(db.placements()) == 1
