"""#2734: 600-file bounded synthetic regression for partial inventory resume.

The measured speedup is demonstrated deterministically by counts of SHA,
EPUB OPF and CRC calls rather than flaky wall-clock thresholds. All file
content, paths and ownership keys are fictitious and remain private.
"""
from __future__ import annotations

import json

from cifereca.core.library_inventory import InventoryLimits, scan_identity_roots
from tests.test_library_reconciliation import epub


def _reusable(result):
    return {(row.identity_id, row.path): row for row in result.files}


def _scan(root, previous=None, until=None):
    stop = [False]

    def progress(counters):
        if until is not None and counters["files"] >= until:
            stop[0] = True

    return scan_identity_roots(
        {"alice": (root,)},
        limits=InventoryLimits(seconds=600),
        cancelled=lambda: stop[0],
        progress=progress,
        _reuse=previous,
    )


def test_six_hundred_files_two_partial_attempts_then_complete(tmp_path):
    root = tmp_path / "PRIVATE_SYNTHETIC_LIBRARY"
    root.mkdir()
    for i in range(599):
        epub(root / f"BOOK_{i:04d}.epub", title=f"PRIVATE_TITLE_{i:04d}")
    pdf = root / "PRIVATE_ONLY_PDF.pdf"
    pdf.write_bytes(b"%PDF-1.7\n1 0 obj\nendobj\n%%EOF\n")
    before = {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
              for p in root.iterdir()}
    assert len(before) == 600

    # First attempt inspects 200. The remaining 400 are *not* missing books.
    first = _scan(root, until=200)
    assert first.reasons == {"cancelled"}
    assert len(first.files) == first.files_started == 200
    assert first.reused_files == 0
    assert first.phase_calls["sha256"] == 200
    assert first.redacted()["status"] == "incomplete_scan"

    # A new attempt reopens/re-enumerates all paths, but reuses 200 positive
    # observations and hashes only 200 more before another cancellation.
    second = _scan(root, _reusable(first), until=400)
    assert second.reasons == {"cancelled"}
    assert len(second.files) == second.files_started == 400
    assert second.reused_files == 200
    assert second.newly_observed_files == 200
    assert second.phase_calls["sha256"] == 200
    assert second.redacted()["status"] == "incomplete_scan"

    # A final attempt reaches every file without redoing completed SHA/OPF/CRC.
    complete = _scan(root, _reusable(second))
    assert complete.reasons == set()
    assert complete.redacted()["status"] == "complete"
    assert len(complete.files) == complete.files_started == 600
    assert complete.reused_files == 400
    assert complete.newly_observed_files == 200
    assert complete.phase_calls["sha256"] == 200
    assert complete.phase_calls["epub_opf"] <= 200
    assert complete.entries == 600
    assert {f.media_format for f in complete.files} == {"epub", "pdf"}
    assert sum(f.media_format == "epub" for f in complete.files) == 599
    assert sum(f.structure_validation == "pdf_structure_minimal"
               for f in complete.files) == 1

    # Identical full run: no SHA, OPF or structure work is repeated at all.
    warm = _scan(root, _reusable(complete))
    assert warm.redacted()["status"] == "complete"
    assert warm.files_started == warm.reused_files == 600
    assert warm.phase_calls["sha256"] == 0
    assert warm.phase_calls["epub_opf"] == 0
    assert warm.phase_calls["structure"] == 0
    assert warm.entries == 600
    assert {f.sha256 for f in warm.files} == {f.sha256 for f in complete.files}

    # One changed EPUB is invalidated without losing the other 599 savings.
    changed = root / "BOOK_0000.epub"
    epub(changed, title="PRIVATE_CHANGED_TITLE")
    modified = _scan(root, _reusable(warm))
    assert modified.redacted()["status"] == "complete"
    assert modified.reused_files == 599
    assert modified.newly_observed_files == 1
    assert modified.revalidated_files == modified.stale_files == 1
    assert modified.phase_calls["sha256"] == 1
    assert modified.phase_calls["epub_opf"] == 1
    assert modified.phase_calls["structure"] == 1
    summary = json.dumps(modified.redacted())
    assert "PRIVATE" not in summary and str(root) not in summary
    assert all(file.sha256 not in summary for file in modified.files)

    # The source filesystem changed only by the explicit synthetic rewrite.
    after = {p.name: (p.stat().st_size, p.stat().st_mtime_ns)
             for p in root.iterdir()}
    assert set(after) == set(before)
    assert all(after[name] == before[name] for name in before
               if name != changed.name)


def test_invalid_epub_is_not_cached_as_valid_or_silently_repaired(tmp_path):
    root = tmp_path / "PRIVATE"
    root.mkdir()
    epub(root / "GOOD.epub")
    broken = root / "BROKEN.epub"
    broken.write_bytes(b"invalid EPUB")
    first = _scan(root)
    assert "epub_parser_limit_or_invalid" in first.reasons
    assert first.redacted()["status"] == "incomplete_scan"
    second = _scan(root, _reusable(first))
    assert second.redacted()["status"] == "incomplete_scan"
    assert second.reused_files == 1
    assert second.newly_observed_files == 1
    assert second.phase_calls["sha256"] == 1
    assert "epub_parser_limit_or_invalid" in second.reasons
    assert broken.read_bytes() == b"invalid EPUB"
    assert "PRIVATE" not in json.dumps(second.redacted())
