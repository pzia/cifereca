"""Offline, fail-closed published-Core release checks (#2095)."""
from __future__ import annotations

import pytest

from scripts.ci_core_release_preflight import ReleaseRefused, validate

SHA = "1" * 40
OTHER = "2" * 40
VERSION = "0.9.44+dev.13"
TAG = "v" + VERSION


def draft(**changes):
    value = {"tag_name": TAG, "draft": True, "target_commitish": SHA}
    value.update(changes)
    return value


def test_new_tag_without_release_can_build_draft():
    assert validate(TAG, VERSION, SHA, None, None) == "new"


def test_existing_same_commit_tag_without_release_can_build():
    assert validate(TAG, VERSION, SHA, SHA, None) == "new"


def test_retry_exact_draft_allowed_with_or_without_created_tag():
    assert validate(TAG, VERSION, SHA, None, draft()) == "same-commit-draft"
    assert validate(TAG, VERSION, SHA, SHA, draft()) == "same-commit-draft"


@pytest.mark.parametrize("existing", [
    {"tag_name": TAG, "draft": False, "target_commitish": SHA},
    draft(target_commitish=OTHER),
    draft(tag_name="v0.9.44+dev.12"),
    draft(draft="true"),
    {},
])
def test_published_retargeted_or_invalid_metadata_refused(existing):
    with pytest.raises(ReleaseRefused):
        validate(TAG, VERSION, SHA, SHA, existing)


def test_tag_cannot_move_even_if_no_release():
    with pytest.raises(ReleaseRefused):
        validate(TAG, VERSION, SHA, OTHER, None)


@pytest.mark.parametrize("tag,version,commit", [
    ("v0.9.44+dev.12", VERSION, SHA),
    (TAG, VERSION, "not-a-sha"),
    ("v0.9.44+dev.13;rm -rf /", VERSION, SHA),
    (TAG, "0.9.44 dev.13", SHA),
])
def test_invalid_release_coordinates_fail_closed(tag, version, commit):
    with pytest.raises(ReleaseRefused):
        validate(tag, version, commit, None, None)


def test_release_preflight_changes_are_never_skipped_by_pr_ci():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/pr-ci.yml").read_text(encoding="utf-8")
    assert '      - "scripts/ci_core_release_preflight.py"' in workflow
    assert r"scripts/ci_core_release_preflight\.py$" in workflow


def test_release_workflow_runs_preflight_before_and_after_build():
    from pathlib import Path
    workflow = (Path(__file__).resolve().parents[1]
                / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert workflow.count("scripts/ci_core_release_preflight.py") == 3
    assert workflow.index("Refuse published Core release") < workflow.index("Build Core wheel")
    assert "Verify downloaded Core release draft assets" in workflow
    assert "sha256sum -c SHA256SUMS" in workflow
    assert "cmp \"dist/$file\" \"$downloaded/$file\"" in workflow
