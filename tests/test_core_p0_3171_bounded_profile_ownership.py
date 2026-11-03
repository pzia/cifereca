"""P0 #3171: private staging has one exact physical profile owner.

No network, supplier credentials or imported private data are required.
"""
from cifereca.storage import Storage
from cifereca.core.sync_coordination import synchronize_account_source
from tests.test_core_p0_3171_canonical_sync_stage import rig  # v2 dependency
from tests.test_core_p0_3719_v2_execution import v2


def test_profile_folder_lookup_is_exact_and_detects_corrupt_duplicate(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        db.identity_put({"identity_id": "A", "label": "A",
                         "directory_name": "profile-A"})
        for i in range(90):
            db.identity_put({"identity_id": f"sibling-{i}", "label": str(i),
                             "directory_name": f"other-{i}"})
        assert db.exact_profile_directory_binding("A") == ("profile-A", True)
        assert db.exact_profile_directory_binding("missing") == (None, False)
        assert db.exact_profile_directory_binding(None) == (None, False)
        # Simulate an imported/old store without the modern UNIQUE index.
        with db._lock, db._connect() as sql:
            sql.execute("DROP INDEX IF EXISTS uq_identity_directory_name")
        db.identity_put({"identity_id": "B", "label": "B",
                         "directory_name": "profile-A"})
        assert db.exact_profile_directory_binding("A") == ("profile-A", False)
        assert db.exact_profile_directory_binding("B") == ("profile-A", False)
        assert db.exact_profile_directory_binding("sibling-75") == ("other-75", True)
    finally:
        db.close()


def test_private_selected_delivery_never_gets_when_profile_folder_ambiguous(v2):
    service, db, root, calls = v2
    with db._lock, db._connect() as sql:
        sql.execute("DROP INDEX IF EXISTS uq_identity_directory_name")
    db.identity_put({"identity_id": "foreign", "label": "foreign",
                     "directory_name": "A"})
    result = synchronize_account_source(service, service.account)
    # The positive A phase now rejects the ambiguous directory *before*
    # handing a snapshot to the convergence executor. No offer lookup and
    # no candidate GET are necessary; absence of execution is intentional.
    assert result["status"] == "partial"
    assert result["canonical_rights"]["status"] == "failed"
    assert "canonical_execution" not in result
    assert calls == []
    assert db.placements() == []
    assert not list((root / "A").rglob("*.pdf"))
