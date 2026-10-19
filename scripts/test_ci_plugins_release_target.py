"""P0 #2095: draft lookup is tag-exact, bounded and fail-closed."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from scripts.ci_plugins_release_target import (
    ReleaseTargetRefused,
    lookup_draft,
    main,
    validate,
)

TAG = "plugins-v0.9.44+dev.13-001"
COMMIT = "20212b37be5f963aed35eee45b338bcdc188aede"


def draft(*, tag=TAG, sha=COMMIT, release_id=417619783, assets=None, is_draft=True):
    return {
        "tag_name": tag, "draft": is_draft, "target_commitish": sha,
        "id": release_id, "assets": [] if assets is None else assets,
        "body": "private URL, token or account: NEVER EXPORTED",
    }


class PluginsDraftLookupTests(unittest.TestCase):
    def test_exact_draft_found_across_pages_with_no_private_output(self):
        pages = [[draft(tag="other", release_id=1)], [draft()]]
        self.assertEqual(lookup_draft(pages, TAG), (417619783, COMMIT, 0))
        self.assertIsNone(lookup_draft([[draft(tag="other")]], TAG))
        stdout = io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps(pages))):
            with contextlib.redirect_stdout(stdout):
                code = main(["--tag", TAG, "--commit", COMMIT, "--lookup-draft"])
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue().strip(), f"417619783 {COMMIT} 0")
        self.assertNotIn("private", stdout.getvalue())

    def test_strict_release_source_after_lookup_is_unchanged(self):
        original = draft()
        validate(original, TAG, COMMIT)
        with self.assertRaises(ReleaseTargetRefused):
            validate(original, TAG, "a" * 40)

    def test_reject_duplicate_drafts_published_malformed_or_ambiguous(self):
        bad = (
            {}, [1], [[None]], [[draft(), draft()]],
            [[draft(is_draft=False)]],
            [[draft(release_id=True)]],
            [[draft(release_id=0)]],
            [[draft(sha="main")]],
            [[{k: v for k, v in draft().items() if k != "assets"}]],
            [[draft(assets="untrusted")]],
            [[draft(assets=[None])]],
        )
        for case in bad:
            with self.subTest(case=str(case)[:55]):
                with self.assertRaises(ReleaseTargetRefused):
                    lookup_draft(case, TAG)

    def test_published_tag_cannot_be_reused_even_with_matching_commit(self):
        with self.assertRaises(ReleaseTargetRefused):
            lookup_draft([[draft(is_draft=False)]], TAG)

    def test_untrusted_cli_fails_without_printing_private_fields(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(json.dumps([[draft(is_draft=False)]]))):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(["--tag", TAG, "--commit", COMMIT, "--lookup-draft"])
        self.assertEqual(code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("private", stderr.getvalue())


class PluginsOnlyWorkflowRecoveryContracts(unittest.TestCase):
    def test_release_workflow_only_rescues_the_attested_empty_2095_draft(self):
        from pathlib import Path
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/plugins-only-release.yml").read_text(encoding="utf-8")
        assert "--paginate --slurp" in workflow
        assert '--lookup-draft' in workflow
        assert '417619783' in workflow
        assert '417644698' in workflow
        assert '1c0ca31762629bdc87c69ae0c39c022ee79fee5f' in workflow
        assert 'await_draft_state() {' in workflow
        assert 'draft_info="$(await_draft_state present)"' in workflow
        assert 'test "$(await_draft_state none)" = "none"' in workflow
        assert 'sleep 2' in workflow
        assert 'GitHub draft metadata did not converge within 24 seconds.' in workflow
        assert COMMIT in workflow
        assert 'git diff --quiet "$draft_sha" "$GITHUB_SHA" --' in workflow
        assert 'src/cifereca src/plugins' in workflow
        assert 'gh api -X DELETE "repos/$GITHUB_REPOSITORY/releases/$draft_id"' in workflow
        assert 'gh api "repos/$GITHUB_REPOSITORY/releases/$draft_id"' in workflow
        assert 'gh api "repos/$GITHUB_REPOSITORY/releases/$P0_RELEASE_ID"' in workflow
        assert '"repos/$GITHUB_REPOSITORY/releases/tags/$RELEASE_TAG"' not in workflow
        assert '--generate-notes' not in workflow
        # Untagged drafts cannot use gh release upload/download/edit by tag.
        assert 'gh release upload "$RELEASE_TAG"' not in workflow
        assert 'gh release download "$RELEASE_TAG"' not in workflow
        assert 'gh release edit "$RELEASE_TAG"' not in workflow
        assert 'https://uploads.github.com/repos/$GITHUB_REPOSITORY/releases/$draft_id/assets' in workflow
        assert 'https://api.github.com/repos/$GITHUB_REPOSITORY/releases/assets/$asset_id' in workflow
        assert 'gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/$P0_RELEASE_ID"' in workflow
        assert 'test "$draft_assets" = "0"' in workflow
        assert 'len(rows) != 3' in workflow
        assert 'assert len(release.get("assets", [])) == 3' in workflow


    def test_release_draft_mutation_step_has_valid_bash_syntax(self):
        """CI must parse the *actual* GitHub Actions shell block before merge."""
        from pathlib import Path
        import subprocess

        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/plugins-only-release.yml").read_text(encoding="utf-8")
        lines = workflow.splitlines()
        position = next(i for i, line in enumerate(lines)
                        if line.strip() == "- name: Create or update draft GitHub Release")
        run = next(i for i in range(position + 1, len(lines))
                   if lines[i] == "        run: |")
        stop = next(i for i in range(run + 1, len(lines))
                    if lines[i].startswith("      - name: "))
        block = lines[run + 1:stop]
        assert block and all(line.startswith("          ") or line == ""
                             for line in block)
        shell = "\n".join(line[10:] if line else "" for line in block) + "\n"
        checked = subprocess.run(["bash", "-n"], input=shell, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 check=False)
        self.assertEqual(checked.returncode, 0, checked.stderr)
