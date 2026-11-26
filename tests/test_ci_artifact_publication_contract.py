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
NODE24_ARTIFACT_WORKFLOWS = frozenset({
    "gtk-smoke.yml",
    "package-plugins.yml",
    "plugins-only-preview.yml",
})
# Explicit review for the bounded E2E-only v7 upload.
EXACT_ARTIFACT_ACTIONS = {"e2e-smoke.yml": "v7.0.2"}

PUBLIC_ARTIFACT_PATHS = {
    "provider-ast-inventory.yml": (
        "provider-ast-responsibilities.json", "provider-core-loc.json",
        "provider-business-coupling.json",
    ),
    "gtk-ubuntu24-cache-asset.yml": (
        ".gtk-publish/gtk4-deb-cache-ubuntu24-noble-amd64.tar.gz",
        ".gtk-publish/SHA256SUMS",
    ),
    "gtk-debian13-cache-asset.yml": (
        ".gtk-publish/gtk4-deb-cache-debian13-trixie-amd64.tar.gz",
        ".gtk-publish/SHA256SUMS",
    ),

    "p0-test-dependency-inventory.yml": (
        "${{ runner.temp }}/cifereca-test-dependencies/core-plugin-consumers.json",
        "${{ runner.temp }}/cifereca-test-dependencies/core-test-provider-imports.json",
        "${{ runner.temp }}/cifereca-test-dependencies/provenance.json",
    ),
    "p0-bounded-test-baseline.yml": (
        "${{ runner.temp }}/bounded-test-baseline/collection.json",
        "${{ runner.temp }}/bounded-test-baseline/execution.json",
        "${{ runner.temp }}/bounded-test-baseline/test-tree.txt",
        "${{ runner.temp }}/bounded-test-baseline/core-plugin-consumers.json",
        "${{ runner.temp }}/bounded-test-baseline/core-test-provider-imports.json",
    ),
    "p0-test-closure-mutations.yml": (
        "${{ runner.temp }}/closure-mutations.json",
    ),
    "e2e-smoke.yml": (
        "${{ runner.temp }}/cifereca-e2e-public/summary.json",
        "${{ runner.temp }}/notice-matrix-receipt.json",
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
        version = EXACT_ARTIFACT_ACTIONS.get(
            workflow.name, "v6" if workflow.name in NODE24_ARTIFACT_WORKFLOWS else "v4"
        )
        action = f"uses: actions/upload-artifact@{version}"
        expected_count = 2 if workflow.name == "e2e-smoke.yml" else 1
        assert data.count("uses: actions/upload-artifact@") == expected_count, workflow.name
        assert data.count(action) == expected_count, workflow.name
        paths = []
        for tail in data.split(action)[1:]:
            block = tail.split("\n      - ", 1)[0]
            paths.extend(_published_paths(block))
            assert re.search(r"(?m)^          retention-days: 2$", block), workflow.name
            assert "          if-no-files-found: " in block, workflow.name
            assert "include-hidden-files: true" not in block, workflow.name
        actual_publishers[workflow.name] = tuple(paths)
    assert actual_publishers == PUBLIC_ARTIFACT_PATHS


def test_reports_are_generated_from_closed_public_verdicts_not_raw_fixtures():
    e2e = (ROOT / "e2e-smoke.yml").read_text(encoding="utf-8")
    assert "scripts/ci-export-e2e-summary.py" in e2e
    assert "cifereca-e2e-public/summary.json" in e2e
    assert "path: e2e-artifacts/" not in e2e
    gtk = (ROOT / "gtk-smoke.yml").read_text(encoding="utf-8")
    assert "gtk-smoke-public/result.json" in gtk
    assert "path: environnements/chatgpt-work/evidence" not in gtk
