"""P0 #1143: uploads must use reviewed, bounded artifact paths only.

This source-only check runs without network or YAML dependencies. A new upload
producer fails closed until its exact output path is explicitly reviewed.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / ".github/workflows"

EXPECTED = {
    "gtk-debian13-cache-asset.yml": (
        (
            ".gtk-publish/gtk4-deb-cache-debian13-trixie-amd64.tar.gz",
            ".gtk-publish/SHA256SUMS",
        ),
    ),

    "e2e-smoke.yml": (
        ("${{ runner.temp }}/cifereca-e2e-public/summary.json",),
    ),
    "gtk-smoke.yml": (
        ("${{ runner.temp }}/gtk-smoke-public/result.json",),
    ),
    "gtk-e2e-on-approval.yml": (
        (
            "/tmp/gtk4-debian13-rootless-offline.tar.gz",
            "/tmp/SHA256SUMS",
        ),
    ),
    "package-plugins.yml": (
        (
            "dist/cifereca-dev-plugins-${{ steps.archives.outputs.sha }}.zip",
            "dist/cifereca-plugins-all-in-one-${{ steps.archives.outputs.sha }}.zip",
        ),
    ),
    "plugins-only-preview.yml": (
        (
            "dist/cifereca-plugins-only-preview-${{ github.run_id }}-${{ github.run_attempt }}.zip",
            "dist/cifereca-plugins-only-preview-${{ github.run_id }}-${{ github.run_attempt }}.zip.sha256",
        ),
    ),
}


def _uploads(source: str) -> tuple[tuple[tuple[str, ...], int], ...]:
    # Each step has six-space indentation. A new, differently laid out
    # artifact action must be reviewed rather than silently accepted.
    blocks = re.split(r"(?m)^      - ", source)[1:]
    all_mentions = source.count("actions/upload-artifact@")
    uploads = []
    for block in blocks:
        if "actions/upload-artifact@" not in block:
            continue
        assert re.search(r"(?m)^\s*uses: actions/upload-artifact@v4\s*$", block)
        assert block.count("actions/upload-artifact@") == 1
        lines = block.splitlines()
        pos = next(
            i for i, line in enumerate(lines)
            if re.match(r"^          path: ", line)
        )
        raw = lines[pos].strip().removeprefix("path: ").strip()
        if raw == "|":
            values = []
            for line in lines[pos + 1:]:
                if not line.startswith("            "):
                    break
                if line.strip():
                    values.append(line.strip())
            paths = tuple(values)
        else:
            paths = (raw,)
        retention = re.findall(r"(?m)^          retention-days: ([0-9]+)\s*$", block)
        assert len(retention) == 1
        assert paths and all(
            path and "*" not in path and ".." not in path and "\n" not in path
            for path in paths
        )
        uploads.append((paths, int(retention[0])))
    assert len(uploads) == all_mentions, "An artifact upload escaped the reviewed step parser"
    return tuple(uploads)


def test_all_workflow_uploads_have_fixed_allowlisted_paths_and_two_day_retention():
    seen = {}
    for workflow in (*ROOT.glob("*.yml"), *ROOT.glob("*.yaml")):
        uploads = _uploads(workflow.read_text(encoding="utf-8"))
        if uploads:
            seen[workflow.name] = tuple(paths for paths, days in uploads)
            assert all(days == 2 for _, days in uploads), workflow.name
    assert seen == EXPECTED


def test_package_upload_names_are_bound_to_exact_git_commit():
    content = (ROOT / "package-plugins.yml").read_text(encoding="utf-8")
    assert 'git rev-parse --short=12 HEAD' in content
    assert 'unzip -tqq "$archive"' in content
    assert 'echo "sha=$sha" >> "$GITHUB_OUTPUT"' in content
    assert "path: dist/*.zip" not in content


def test_no_untrusted_e2e_capture_reaches_actions():
    e2e = (ROOT / "e2e-smoke.yml").read_text(encoding="utf-8")
    assert 'scripts/ci-export-e2e-summary.py' in e2e
    assert 'cat "$RUNNER_TEMP/cifereca-e2e-public/summary.json"' in e2e


def test_official_core_release_rejects_unreviewed_assets_before_upload():
    workflow = (ROOT / "release.yml").read_text(encoding="utf-8")
    assert "Unexpected Core release artifact set; refusing publication." in workflow
    assert "find dist -mindepth 1 -maxdepth 1" in workflow
    assert "git rev-parse --short=12 HEAD" in workflow
    assert 'test ! -L "dist/$file"' in workflow
    for name in ("RELEASE_WHEEL", "RELEASE_DEV", "RELEASE_BUNDLE"):
        assert f'dist/${name}' in workflow
    assert 'sha256sum "$wheel" "$dev" "$bundle" > SHA256SUMS' in workflow
    assert 'gh release upload "$RELEASE_TAG" dist/*' not in workflow
    assert "Verify downloaded Core release draft assets" in workflow
