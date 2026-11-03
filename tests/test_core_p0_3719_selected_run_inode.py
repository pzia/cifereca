"""P0 #3719/#3171: selected delivery must pin its Core-owned run inode.

Real Core A→B executor and v2 provider candidate contract, disposable PDF and
SQLite. No network, ADEPT or user files. A new directory under the original
random run name is not the namespace authorized by the Core.
"""
from dataclasses import replace

from tests.test_core_p0_3171_canonical_sync_stage import refs, rig
from tests.test_core_p0_3171_atomic_publication_v14 import BYTES
from tests.test_core_p0_3719_v2_execution import v2
from cifereca.core.sync_coordination import synchronize_account_source


def test_provider_cannot_swap_core_staging_run_for_new_same_name_directory(v2):
    service, db, root, calls = v2
    original = service.session.downloadable_selected_delivery_candidate
    staged = {}

    def swapped(*args, **kwargs):
        received = original(*args, **kwargs)
        attempt = kwargs["target_dir"]
        moved = attempt.with_name("detached-" + attempt.name)
        attempt.rename(moved)
        # The producer returned plausible valid bytes from a *different*
        # directory inode with the SAME Core-selected pathname.
        attempt.mkdir(mode=0o700)
        replacement = attempt / "received.pdf"
        replacement.write_bytes(BYTES)
        staged.update(attempt=attempt, moved=moved, replacement=replacement)
        return replace(received, path=replacement)

    service.session.downloadable_selected_delivery_candidate = swapped
    result = synchronize_account_source(service, service.account)
    assert calls == ["offer", "delivery"]
    assert result["canonical_execution"]["downloaded"] == 0
    assert result["canonical_execution"]["failed"] == 1
    assert refs(db)[1] == 0
    assert db.placements() == []
    assert db.entitlements() == []
    assert not any((root / "A" / "Acquisitions").glob("*.pdf"))
    # A failed cleanup must NOT delete this new third-party namespace.
    assert staged["replacement"].read_bytes() == BYTES
    assert (staged["moved"] / "received.pdf").read_bytes() == BYTES
