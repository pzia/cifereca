"""#3185: post-publication checkpoint cleanup must not delete foreign bytes.

A local A→B send is allowed to delete only its exact staged inode.
SQLite/file fixtures are disposable, and no upstream operation is invoked.
"""
from pathlib import Path

import pytest

from cifereca.core.local_transfer_recovery import LocalTransferCheckpoint
from tests.test_core_p1_3275_live_canonical_transfer import BYTES, canon_rows
from tests.test_local_transfer_precommit_recovery import case


@pytest.mark.parametrize("replacement", ["file", "symlink"])
def test_checkpoint_finish_refuses_substituted_temporary_inode(
    case, tmp_path, monkeypatch, replacement,
):
    service, root, source, placement = case
    original_pin = LocalTransferCheckpoint._pin
    marker = tmp_path / "foreign-original"
    payload = b"unrelated original temporary entry"
    marker.write_bytes(payload)
    substitutions = []

    def pin_then_replace(self, path):
        pin = original_pin(self, path)
        # record_ready (pre-commit) also pins this temporary file. Inject
        # only in finish(), after canonical publication, at the precise
        # boundary between validation and the historical blind unlink.
        if (
            not substitutions
            and path.name.startswith(".cifereca-send-")
            and any(
                row.get("status") == "local_transfer_published"
                for row in self.storage.acquisitions()
            )
        ):
            path.unlink()
            if replacement == "file":
                marker.rename(path)
            else:
                path.symlink_to(marker)
            substitutions.append(path)
        return pin

    monkeypatch.setattr(LocalTransferCheckpoint, "_pin", pin_then_replace)
    with pytest.raises(ValueError, match="local_transfer_copy_changed"):
        service.materialize_to_identity(placement, "bob")

    assert len(substitutions) == 1
    temporary = substitutions[0]
    assert temporary.is_symlink() if replacement == "symlink" else temporary.is_file()
    assert temporary.read_bytes() == payload
    if replacement == "symlink":
        assert marker.read_bytes() == payload
    assert (root / "bob" / "Acquisitions" / "Synthetic book.pdf").read_bytes() == BYTES
    assert source.read_bytes() == BYTES
    assert canon_rows(service.storage)["origin_links"] == 2
    assert canon_rows(service.storage)["placements"] == 2
    assert service.storage.entitlements() == []
