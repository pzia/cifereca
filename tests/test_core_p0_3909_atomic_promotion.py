"""P0 #3909: the canonical final name must never contain half a download.

Real SQLite v14 and one synthetic provider source Sync; no supplier network.
Staging receipt survives a failed promotion and resumes without a second GET.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from cifereca.core import canonical_promotion as promotion
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_selected_candidate_finalization import (
    rig, _isolate_delivery_lock, _prepare, _native,
)
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES


def _drive(rig):
    service, db, root = rig
    _prepare(service)
    requests = []

    def candidate(*args, **kwargs):
        requests.append(kwargs["delivery_offer_id"])
        return _native(*args, **kwargs)

    service.session.rights_selected_delivery_candidate = candidate
    return service, db, root, requests


def _destination(root):
    return root / "A" / "Acquisitions"


def test_copy_is_invisible_until_complete_and_atomic_hardlink(rig, monkeypatch):
    service, db, root, requests = _drive(rig)
    real_link = os.link
    observations = []

    def watch_link(source, target, **kw):
        if str(source).startswith(".canonical-promote-"):
            dest = _destination(root)
            assert not (dest / target).exists()
            assert not db.placements()
            partials = list(dest.glob(".canonical-promote-*.part"))
            assert len(partials) == 1
            assert partials[0].read_bytes() == BYTES
            observations.append("verified_copy_before_final")
        return real_link(source, target, **kw)

    monkeypatch.setattr(promotion.os, "link", watch_link)
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["downloaded"] == 1
    assert requests == ["signed-right-offer"]
    assert observations == ["verified_copy_before_final"]
    placement = Path(db.placements()[0]["path"])
    assert placement.parent == _destination(root)
    assert placement.read_bytes() == BYTES
    assert list(_destination(root).glob("*.part")) == []
    again = synchronize_account_source(service, service.account)
    assert again["canonical_execution"]["present_attested"] == 1
    assert requests == ["signed-right-offer"]


def test_atomic_link_error_preserves_exact_staging_for_retry(rig, monkeypatch):
    service, db, root, requests = _drive(rig)
    real_link = os.link
    failures = []

    def unavailable(source, target, **kw):
        if str(source).startswith(".canonical-promote-") and not failures:
            failures.append(1)
            raise OSError("synthetic atomic link failure")
        return real_link(source, target, **kw)

    monkeypatch.setattr(promotion.os, "link", unavailable)
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["failed"] == 1
    assert db.placements() == []
    assert not list(_destination(root).iterdir())
    staged = list((root / "A" / ".canonical-delivery").glob("run-*/received.pdf"))
    assert len(staged) == 1 and staged[0].read_bytes() == BYTES

    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["recovered"] == 1
    assert requests == ["signed-right-offer"]
    assert len(db.placements()) == 1
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES
    assert not staged[0].exists()


def test_foreign_target_created_during_atomic_link_is_never_replaced(
    rig, monkeypatch,
):
    service, db, root, requests = _drive(rig)
    real_link = os.link
    foreign = []

    def contested(source, target, **kw):
        if str(source).startswith(".canonical-promote-") and not foreign:
            dest = _destination(root) / target
            dest.write_bytes(b"foreign-incomplete-file")
            foreign.append(dest)
        return real_link(source, target, **kw)

    monkeypatch.setattr(promotion.os, "link", contested)
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["failed"] == 1
    assert not db.placements()
    assert len(foreign) == 1 and foreign[0].read_bytes() == b"foreign-incomplete-file"
    assert list(_destination(root).glob(".canonical-promote-*.part")) == []
    assert requests == ["signed-right-offer"]

    # This is a conflicting path, not a reason to fetch from the supplier.
    foreign[0].unlink()
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["recovered"] == 1
    assert requests == ["signed-right-offer"]
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES


def test_fsync_failure_after_link_withdraws_final_and_keeps_staging(
    rig, monkeypatch,
):
    service, db, root, requests = _drive(rig)
    real_fsync = os.fsync
    faults = []

    def failing_fsync(fd):
        # The directory fsync follows the hardlink; its failure must not
        # leave a final-looking partially committed file.
        if (not faults and _destination(root).is_dir()
                and list(_destination(root).glob("*.pdf"))):
            faults.append(1)
            raise OSError("synthetic directory flush failure")
        return real_fsync(fd)

    monkeypatch.setattr(promotion.os, "fsync", failing_fsync)
    first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["failed"] == 1
    assert not db.placements()
    assert faults == [1]
    assert list(_destination(root).iterdir()) == []

    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["recovered"] == 1
    assert requests == ["signed-right-offer"]
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES



@pytest.mark.parametrize("change_at", ["before_link", "after_link"])
def test_promotion_never_leaves_final_file_after_identity_folder_rebound(
    rig, monkeypatch, change_at,
):
    """A registry rewrite during a large file copy is not a publishing grant."""
    service, db, root, requests = _drive(rig)
    real_fsync = os.fsync
    real_link = os.link
    changed = []

    def reassign():
        if changed:
            return
        with db._lock, db._connect() as sql:
            sql.execute(
                "UPDATE identities SET directory_name='rebound' "
                "WHERE identity_id='A'"
            )
        changed.append(True)

    def mutate_during_flush(fd):
        if (change_at == "before_link" and not changed
                and list(_destination(root).glob(".canonical-promote-*.part"))):
            reassign()
        return real_fsync(fd)

    def mutate_during_link(source, target, **kwargs):
        linked = real_link(source, target, **kwargs)
        if (change_at == "after_link" and not changed
                and str(source).startswith(".canonical-promote-")):
            reassign()
        return linked

    with monkeypatch.context() as patch:
        patch.setattr(promotion.os, "fsync", mutate_during_flush)
        patch.setattr(promotion.os, "link", mutate_during_link)
        first = synchronize_account_source(service, service.account)
    assert first["canonical_execution"]["failed"] == 1
    assert changed == [True]
    assert db.placements() == []
    assert list(_destination(root).iterdir()) == []
    staged = list((root / "A" / ".canonical-delivery").glob("run-*/received.pdf"))
    assert len(staged) == 1 and staged[0].read_bytes() == BYTES
    assert requests == ["signed-right-offer"]

    # Repair the original identity registry: the same admitted receipt
    # resumes without another provider GET or adopting foreign files.
    with db._lock, db._connect() as sql:
        sql.execute("UPDATE identities SET directory_name='A' WHERE identity_id='A'")
    second = synchronize_account_source(service, service.account)
    assert second["canonical_execution"]["recovered"] == 1
    assert requests == ["signed-right-offer"]
    assert Path(db.placements()[0]["path"]).read_bytes() == BYTES
