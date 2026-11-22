"""#2435: 16 manifest names must not count one hard-linked archive 16 times."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_epub_cover_contract import _write_epub


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only benchmark")
def test_sixteen_hardlinks_cannot_certify_representative_n3_corpus(tmp_path, monkeypatch):
    archive = tmp_path / "PRIVATE_ORIGINAL.epub"
    digest = hashlib.sha256(_write_epub(archive, epub3=True)).hexdigest()
    others = []
    for index in range(15):
        destination = tmp_path / f"PRIVATE_DUPLICATE_{index}.epub"
        os.link(archive, destination)
        others.append(destination)
    assert len({os.stat(path).st_ino for path in [archive, *others]}) == 1
    manifest = tmp_path / "PRIVATE_MANIFEST.json"
    manifest.write_text(json.dumps([str(path) for path in (archive, *others)]))
    user_cache = tmp_path / "untouched-user-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(user_cache))
    script = Path(__file__).resolve().parents[1] / "scripts/benchmark-notice-cover-epub.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--size", "16", "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=90,
    )
    assert proc.returncode == 1
    assert "PRIVATE" not in (proc.stdout + proc.stderr)
    report = json.loads(proc.stdout)
    assert report["selected"] == 16
    assert report["qualified"] == 1
    assert report["complete_manifest"] is False and report["valid"] is False
    assert report["skipped"] == {"duplicate_physical_file": 15}
    assert report["distinct_cover_media"] == 1
    assert report["phases"]["n3_cache_cold"]["n3_miss"] == 1
    assert not user_cache.exists()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path in (archive, *others))
