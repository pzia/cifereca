"""#3275: an inventory scan must not erase physical transfer provenance."""
import os
from pathlib import Path

import pytest

from cifereca.plugin_api import write_bundle_manifest
from tests.test_catalog_083 import _epub, _service


@pytest.mark.parametrize("bundle", [False, True])
def test_send_scan_resend_preserves_one_b_copy_and_no_rights(tmp_path, bundle):
    service = _service(str(tmp_path))
    try:
        parent = tmp_path / "EBooks" / "hugues" / "local"
        parent.mkdir(parents=True)
        src = parent / ("Audio" if bundle else "Book.epub")
        if bundle:
            src.mkdir()
            (src / "track.mp3").write_bytes(b"audio")
            (src / "play.m3u8").write_text("#EXTM3U\ntrack.mp3\n")
            write_bundle_manifest(src, title="Audio", playlist="play.m3u8")
        else:
            _epub(src)
        service.catalog_scan()
        matches = [p for p in service.storage.placements(state="present")
                   if p["path"] == str(src)]
        assert len(matches) == 1
        source_id = matches[0]["placement_id"]

        first = service.materialize_to_identity(source_id, "alex")
        dest = Path(first["path"])
        assert dest.exists() and not os.path.samefile(src, dest)
        before = [r for r in service.storage.catalog_entries(identity_id="alex")
                  if r["local_state"] == "local"]
        assert len(before) == 1

        service.catalog_scan()
        saved = service.storage.placement_get(first["placement_id"])
        assert saved["metadata"]["materialized_from"] == source_id
        assert saved["metadata"]["file_origin_identity_id"] == "default"
        assert saved["metadata"]["transfer_kind"] == "local_copy"
        after = [r for r in service.storage.catalog_entries(identity_id="alex")
                 if r["local_state"] == "local"]
        assert len(after) == 1 and after[0]["catalog_entry_id"] == before[0]["catalog_entry_id"]

        again = service.materialize_to_identity(source_id, "alex")
        assert again["status"] == "unchanged"
        assert again["path"] == first["path"]
        assert again["placement_id"] == first["placement_id"]
        assert service.storage.entitlements() == []
        records = service.storage.acquisitions()
        if bundle:
            assert records == []
        else:
            # A completed copy checkpoint is operation history, never an
            # acquisition receipt or a supplier entitlement for the recipient.
            record, = records
            assert (record["provider_id"], record["credential_id"],
                    record["account_id"], record["identity_id"]) == (
                "cifereca", "local", "alex", "alex",
            )
            assert record["status"] == "local_transfer_published"
            assert record["strategy"] == "local_transfer"
            assert record["artifact_id"] is None and record["entitlement_id"] is None
    finally:
        service.close()
