"""Smoke-test the opt-in benchmark on a synthetic, private scratch corpus."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tests.test_epub_cover_contract import _write_epub
from tests.test_core_p0_2435_cover_cache_baseline import _png
from tests import test_epub_cover_contract as fixture


@pytest.mark.skipif(sys.platform != "linux", reason="Linux process I/O metrics")
def test_manifest_benchmark_reports_aggregate_without_touching_inputs(tmp_path, monkeypatch):
    paths = []
    before = {}
    for index in range(15):
        path = tmp_path / f"PRIVATE_BOOK_{index}.epub"
        monkeypatch.setattr(fixture, "PNG", _png(index))
        before[path] = hashlib.sha256(_write_epub(path, epub3=True)).hexdigest()
        paths.append(path)
    invalid = tmp_path / "PRIVATE_INVALID.epub"
    invalid.symlink_to(paths[0])
    paths.append(invalid)
    manifest = tmp_path / "private.json"
    manifest.write_text(json.dumps([str(path) for path in paths]))
    script = Path(__file__).resolve().parents[1] / "scripts/benchmark-notice-cover-epub.py"
    production_cache = tmp_path / "untouched-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(production_cache))
    proc = subprocess.run(
        [sys.executable, str(script), "--size", "16", "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=90,
    )
    # 15 valid EPUBs out of the requested 16 are measurable, but must
    # never certify a complete representative baseline.
    assert proc.returncode == 1
    assert "PRIVATE" not in proc.stdout and "PRIVATE" not in proc.stderr
    report = json.loads(proc.stdout)
    assert report["selected"] == 16 and report["qualified"] == 15
    assert report["complete_manifest"] is False and report["valid"] is False
    assert sum(report["skipped"].values()) == 1
    assert report["distinct_cover_media"] == 15
    assert report["phases"]["n3_cache_cold"]["n3_miss"] == 15
    assert report["phases"]["n3_cache_warm"]["n3_hit"] == 15
    assert report["phases"]["n3_cache_warm"]["n3_miss"] == 0
    assert not report["os_cache_flushed"] and not production_cache.exists()
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in before.items())
    assert invalid.is_symlink()


    # After replacing the rejected symlink with a real, independently
    # readable EPUB, the exact same 16-path manifest can produce a PASS.
    invalid.unlink()
    monkeypatch.setattr(fixture, "PNG", _png(15))
    before[invalid] = hashlib.sha256(_write_epub(invalid, epub3=True)).hexdigest()
    proc_complete = subprocess.run(
        [sys.executable, str(script), "--size", "16", "--manifest", str(manifest)],
        capture_output=True, text=True, timeout=90,
    )
    assert proc_complete.returncode == 0
    assert "PRIVATE" not in proc_complete.stdout + proc_complete.stderr
    complete = json.loads(proc_complete.stdout)
    assert complete["complete_manifest"] is True and complete["valid"] is True
    assert complete["selected"] == complete["qualified"] == 16
    assert complete["phases"]["n3_cache_cold"]["n3_miss"] == 16
    assert complete["phases"]["n3_cache_warm"]["n3_hit"] == 16
    assert all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
               for path, digest in before.items())
