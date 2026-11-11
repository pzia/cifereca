"""#3196: source phase history must not depend on retired Kobo W2 state."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca"


def test_w2_checkpoint_reader_and_engine_are_removed():
    assert not (ROOT / "core/legacy_checkpoint_read.py").exists()
    assert not (ROOT / "core/kobo_backup.py").exists()


def test_phase_broker_never_imports_retired_checkpoint_reader():
    tree = ast.parse((ROOT / "brokers.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module in {"core.legacy_checkpoint_read", "core.kobo_backup"}
        for node in ast.walk(tree)
    )
