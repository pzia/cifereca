"""#3196: canonical Sync cannot retire Kobo 0.7 archives as a side effect.

Historical backup_items remain inert storage, not durable rights or presence.
Existing canonical convergence tests exercise A -> B without this maintenance.
"""
from __future__ import annotations

import ast
from pathlib import Path


CORE = Path(__file__).resolve().parents[1] / "src/cifereca/core"


def test_retired_legacy_archive_engines_are_not_shipped():
    assert not (CORE / "legacy_backup_retirement.py").exists()
    assert not (CORE / "legacy_backup_publication_review.py").exists()


def test_canonical_convergence_has_no_kobo_archive_retirement_dispatch():
    source = (CORE / "convergence_execution.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "retire_legacy_if_verified" not in source
    assert "legacy_archive_exactly_retired" not in source
    assert "legacy_archive_retirement_deferred" not in source
    assert "legacy_backup_retirement" not in source
    assert "FROM backup_items" not in source
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module in {
            "legacy_backup_retirement", "legacy_backup_publication_review",
        }
        for node in ast.walk(tree)
    )
    assert "plan_existing_verified_presence" in source
    assert "publish" in source
