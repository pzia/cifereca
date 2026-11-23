"""Restart an independent process across the rename writer's commit boundary."""
from contextlib import closing
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import sys

import pytest

from cifereca.storage import Storage
from tests.test_core041_artifact_authority import Control
from tests.test_core_p0_2789_crash_consistent_normalize import _case


def crash_writer(root, boundary):
    service, _, broker, plan, _, _, _ = _case(root)
    if boundary == "before_commit":
        def terminate_before_commit(statement):
            # sqlite invokes this callback before executing COMMIT, after
            # the real relocation has updated the rows in its transaction.
            if statement == "COMMIT":
                os._exit(41)

        service.storage._connect().set_trace_callback(terminate_before_commit)
    else:
        relocate = service.storage.relocate_local_path

        def terminate_after_commit(*args, **kwargs):
            relocate(*args, **kwargs)
            os._exit(42)

        service.storage.relocate_local_path = terminate_after_commit
    broker.run(plan, Control())
    raise AssertionError("writer did not reach the expected interruption")


@pytest.mark.parametrize("boundary,expected_exit,name", [
    ("before_commit", 41, "Book.epub"),
    ("after_commit", 42, "Author - Book.epub"),
])
def test_fresh_storage_resolves_authoritative_bytes_after_writer_exit(
        tmp_path, boundary, expected_exit, name):
    repo = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join((str(repo / "src"), *sys.path)),
    }
    program = (
        "import sys; from pathlib import Path; "
        "from tests.test_core_p0_3176_rename_process_restart import crash_writer; "
        "crash_writer(Path(sys.argv[1]), sys.argv[2])"
    )
    result = subprocess.run(
        [sys.executable, "-c", program, str(tmp_path), boundary],
        cwd=repo, env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == expected_exit, result.stderr
    root = tmp_path / "library"
    old, new = root / "Book.epub", root / "Author - Book.epub"
    assert old.samefile(new)
    with closing(Storage(tmp_path / "db.sqlite3")) as storage:
        placement, = storage.placements()
        entry, = storage.catalog_entries()
        named = root / name
        assert placement["path"] == entry["metadata"]["path"] == str(named)
        assert entry["placement_id"] == placement["placement_id"]
        assert placement["identity_id"] == "id1" and placement["source_item_id"] == "book-1"
        assert named.read_bytes() == b"book-data"
        assert placement["artifact_id"] == "sha256:" + sha256(named.read_bytes()).hexdigest()
        assert storage.acquisitions() == [] and storage.entitlements() == []
