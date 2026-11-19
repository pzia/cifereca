"""Local description packets never cross owner or Placement boundaries."""
from cifereca.core.notice_c_purchase_library_projection import (
    PurchaseLibraryNotice, purchase_library_notice,
)
from cifereca.core.notice_c_purchase_descriptive_read import PurchaseDescriptiveNotice
from tests.test_core_p0_1072_durable_purchase_intent import prepare
from cifereca.storage import Storage


def test_local_packet_exact_placement(tmp_path):
    db = Storage(tmp_path / "local.sqlite")
    try:
        _, candidate = prepare(db)
        notice = PurchaseDescriptiveNotice(None, 1, 100, ())
        packet = PurchaseLibraryNotice("local:a", candidate.source, notice, ("a", "p"))
        row = {"key": "local:a", "identity_id": candidate.source.scope.identity_id,
               "local": True, "state": "local", "artifact_id": "a", "placement_id": "p",
               "_purchase_library_notice": packet}
        assert purchase_library_notice(row) is packet
        for field, value in (("identity_id", "B"), ("placement_id", "q"),
                             ("artifact_id", "b"), ("local", False), ("key", "other")):
            assert purchase_library_notice({**row, field: value}) is None
    finally:
        db.close()
