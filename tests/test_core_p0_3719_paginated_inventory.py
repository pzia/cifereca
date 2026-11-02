"""P0 #3719/#3722: the one Core Sync consumes all positive inventory pages.

Only synthetic provider responses and disposable SQLite; no DRM or network.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.core.rights_snapshot_read import read_account_rights_snapshot
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api.provider_rights_observation import (
    ProviderRightObservation, ProviderRightsSnapshot, ProviderRightsScope,
)
from tests.test_core_p0_3171_rights_snapshot_read import rig as reader_rig, STAMP
from tests.test_core_p0_3171_canonical_sync_stage import rig as sync_rig, refs


def _pages(scope, *, count=350, page_size=50, change=None):
    calls = []

    def reader(_account, *, resume_cursor=""):
        calls.append(resume_cursor)
        start = int(resume_cursor.split(":")[1]) if resume_cursor else 0
        if change == "interrupted" and start == page_size:
            raise RuntimeError("PRIVATE-cursor-or-signed-url")
        if change == "failed" and start == page_size:
            return ProviderRightsSnapshot(scope, status="failed")
        if change == "replay" and start == page_size:
            start = 0
        if change == "rebound" and start == page_size:
            scope_for_page = replace(scope, account_id="other-borrower")
        else:
            scope_for_page = scope
        if change == "branch" and start == page_size:
            branch = "foreign"
        else:
            branch = "purchases"
        stop = min(start + page_size, count)
        observed = tuple(ProviderRightObservation(
            f"edition-{i}", f"right-{i}", "purchase", "unknown",
            "provider_response", "synthetic-remote", STAMP,
        ) for i in range(start, stop))
        return ProviderRightsSnapshot(
            scope_for_page, observed, status="partial", coverage="partial",
            branch=branch, resume_cursor=f"offset:{stop}" if stop < count else "",
        )
    return reader, calls


def test_core_reads_all_350_references_without_an_extra_execution_loop():
    service, account, session = reader_rig()
    reader, calls = _pages(ProviderRightsScope(
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    ))
    session.rights_observation_snapshot = reader
    result = read_account_rights_snapshot(service, account)
    assert result.status == result.coverage == "partial"
    assert len(result.rights) == 350
    assert result.resume_cursor == ""
    assert calls == [""] + [f"offset:{i}" for i in range(50, 350, 50)]
    assert len({(x.edition_id, x.right_id, x.kind) for x in result.rights}) == 350
    assert not result.claims_complete_branch
    assert not result.authorizes_negative_reconciliation


@pytest.mark.parametrize(("change", "expected_status", "observed"), [
    ("interrupted", "partial", 50),
    ("failed", "partial", 50),
    ("replay", "partial", 50),
    ("branch", "failed", 0),
    ("rebound", "failed", 0),
])
def test_bad_second_page_never_invents_rights_or_negative_evidence(
    change, expected_status, observed,
):
    service, account, session = reader_rig()
    reader, calls = _pages(ProviderRightsScope(
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    ), change=change)
    session.rights_observation_snapshot = reader
    result = read_account_rights_snapshot(service, account)
    assert result.status == expected_status
    assert len(result.rights) == observed
    assert calls == ["", "offset:50"]
    assert not result.authorizes_negative_reconciliation
    assert result.resume_cursor == ("offset:50" if observed else "")


def test_core_sync_persists_all_350_scoped_positive_references(
    sync_rig,
):
    service, db, root = sync_rig
    sample = service.snapshot
    calls = []

    def inventory(_account, *, resume_cursor=""):
        calls.append(resume_cursor)
        start = int(resume_cursor.split(":")[1]) if resume_cursor else 0
        stop = min(start + 50, 350)
        return replace(
            sample, rights=tuple(replace(
                sample.rights[0], edition_id=f"edition-{index}",
                right_id=f"right-{index}", state="unknown",
            ) for index in range(start, stop)),
            status="partial", coverage="partial",
            resume_cursor=f"offset:{stop}" if stop < 350 else "",
        )

    service.session.rights_observation_snapshot = inventory
    result = synchronize_account_source(service, service.account)
    assert result["canonical_rights"]["observed"] == 350
    assert result["canonical_rights"]["attested_active"] == 350
    assert result["canonical_rights"]["source_references_changed"] == 350
    assert result["canonical_rights"]["pending"] == 350
    assert len(refs(db)[0]) == 350
    assert calls == [""] + [f"offset:{i}" for i in range(50, 350, 50)]
    assert db.placements() == []  # No acquisition without source consent.



def test_versioned_v2_inventory_pages_follow_the_same_core_reader():
    from cifereca.plugin_api.downloadable_resources import (
        from_legacy_rights_snapshot,
    )

    service, account, session = reader_rig()
    reader, calls = _pages(ProviderRightsScope(
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    ), count=351)
    session.downloadable_resource_snapshot = (
        lambda selected, *, resume_cursor="":
            from_legacy_rights_snapshot(
                reader(selected, resume_cursor=resume_cursor)
            )
    )
    result = read_account_rights_snapshot(service, account)
    assert result.status == "partial"
    assert len(result.rights) == 351
    assert result.resume_cursor == ""
    assert calls == [""] + [f"offset:{n}" for n in range(50, 351, 50)]
    assert not result.authorizes_negative_reconciliation
