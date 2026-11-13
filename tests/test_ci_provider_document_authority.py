"""Focused regression tests for the provider document authority gate (#5519)."""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts import ci_provider_document_authority as gate


@pytest.fixture
def authority_tree(tmp_path: Path) -> Path:
    """Minimal local tree, independent of history/other repository notes."""
    sources = (
        gate.INDEX, gate.SOURCE, gate.COPY,
        *gate.AUTHORITIES, *gate.ENTRYPOINTS,
    )
    for source in set(sources):
        target = tmp_path / source.relative_to(gate.ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    for link in gate.INDEX_LINKS:
        target = tmp_path / "docs" / link
        if link.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    assert gate.check(tmp_path) == []
    return tmp_path


def test_repository_document_authorities_are_coherent():
    assert gate.check() == []


def test_gate_fails_closed_on_devkit_copy_drift(authority_tree):
    copied = authority_tree / gate.COPY.relative_to(gate.ROOT)
    copied.write_bytes(copied.read_bytes() + b"drift")
    assert any("differs byte-for-byte" in error
               for error in gate.check(authority_tree))


def test_gate_fails_on_missing_normative_reference(authority_tree):
    index = authority_tree / gate.INDEX.relative_to(gate.ROOT)
    content = index.read_text(encoding="utf-8")
    assert "](ARCHITECTURE.md)" in content
    index.write_text(
        content.replace("](ARCHITECTURE.md)", "](old-architecture.md)", 1),
        encoding="utf-8",
    )
    assert any("Missing normative index link: ARCHITECTURE.md" in error
               for error in gate.check(authority_tree))


def test_gate_fails_on_agent_role_missing_top_level_reference(authority_tree):
    role = authority_tree / ".agents/roles/core.md"
    role.write_text("# Core\n\nNo document authority here\n", encoding="utf-8")
    assert any("Entrypoint does not prioritize index: .agents/roles/core.md"
               in error for error in gate.check(authority_tree))


def test_gate_ignores_historical_terminology_not_in_scope(authority_tree):
    historical = authority_tree / "docs" / "CORE-P0-3196-LEGACY-proof.md"
    historical.write_text(
        "rights / Entitlement / backup_items historical observations\n",
        encoding="utf-8",
    )
    assert gate.check(authority_tree) == []
