"""P0 #1143: explicit privacy boundary for every Actions artifact producer.

Only the reviewed files may leave ephemeral CI runners. Additions require
updating this test deliberately, rather than publishing raw diagnostics by
an unnoticed wildcard or implicit 90-day default retention.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / ".github/workflows"

# Exact path expressions from the reviewed workflows (not globbed directories).
PUBLIC_ARTIFACT_PATHS = {
    "gtk-debian13-cache-asset.yml": (
        ".gtk-publish/gtk4-deb-cache-debian13-trixie-amd64.tar.gz",
        ".gtk-publish/SHA256SUMS",
    ),

    "e2e-smoke.yml": (
        "${{ runner.temp }}/cifereca-e2e-public/summary.json",
    ),
    "gtk-smoke.yml": (
        "${{ runner.temp }}/gtk-smoke-public/result.json",
    ),
    "gtk-e2e-on-approval.yml": (
        "/tmp/gtk4-debian13-rootless-offline.tar.gz",
        "/tmp/SHA256SUMS",
    ),
    "package-plugins.yml": (
        "dist/cifereca-dev-plugins-${{ steps.archives.outputs.sha }}.zip",
        "dist/cifereca-plugins-all-in-one-${{ steps.archives.outputs.sha }}.zip",
    ),
    "plugins-only-preview.yml": (
        "dist/cifereca-plugins-only-preview-${{ github.run_id }}-${{ github.run_attempt }}.zip",
        "dist/cifereca-plugins-only-preview-${{ github.run_id }}-${{ github.run_attempt }}.zip.sha256",
    ),
}


def _published_paths(block: str) -> tuple[str, ...]:
    lines = block.splitlines()
    indices = [i for i, line in enumerate(lines) if line.startswith("          path: ")]
    assert len(indices) == 1, "Every artifact must have one explicit path"
    first = lines[indices[0]].split("path: ", 1)[1]
    if first != "|":
        return (first.strip(),)
    paths = []
    for line in lines[indices[0] + 1:]:
        if not line.startswith("            "):
            break
        paths.append(line.strip())
    return tuple(paths)


def test_exact_public_artifact_allowlist_and_two_day_retention():
    actual_publishers = {}
    for workflow in ROOT.glob("*.yml"):
        data = workflow.read_text(encoding="utf-8")
        if "actions/upload-artifact" not in data:
            continue
        # Every producer is intentionally reviewed, including manual jobs.
        assert data.count("uses: actions/upload-artifact@v4") == 1, workflow.name
        block = data.split("uses: actions/upload-artifact@v4", 1)[1].split(
            "\n      - ", 1,
        )[0]
        actual_publishers[workflow.name] = _published_paths(block)
        assert re.search(r"(?m)^          retention-days: 2$", block), workflow.name
        assert "          if-no-files-found: " in block, workflow.name
        assert "include-hidden-files: true" not in block, workflow.name
    assert actual_publishers == PUBLIC_ARTIFACT_PATHS


def test_reports_are_generated_from_closed_public_verdicts_not_raw_fixtures():
    e2e = (ROOT / "e2e-smoke.yml").read_text(encoding="utf-8")
    assert "scripts/ci-export-e2e-summary.py" in e2e
    assert "cifereca-e2e-public/summary.json" in e2e
    assert "path: e2e-artifacts/" not in e2e
    gtk = (ROOT / "gtk-smoke.yml").read_text(encoding="utf-8")
    assert "gtk-smoke-public/result.json" in gtk
    assert "path: environnements/chatgpt-work/evidence" not in gtk
