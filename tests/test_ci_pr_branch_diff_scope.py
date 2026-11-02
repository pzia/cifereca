"""CI: use PR branch-only changed files even when main advanced.

Everything runs against temporary Git repositories; no remote/credentials.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/pr-ci.yml"


def git(root, *args, check=True):
    p = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=check,
    )
    return p.stdout.strip() if check else p


class PRDiffScopeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name)
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.name", "CI")
        git(self.repo, "config", "user.email", "ci@example.invalid")
        (self.repo / "README.md").write_text("initial\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-m", "initial")

    def change(self, path, content, *, message=None):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        git(self.repo, "add", path)
        git(self.repo, "commit", "-m", message or path)

    def test_main_only_changes_are_not_selected_into_pr_scopes(self):
        git(self.repo, "checkout", "-b", "feature")
        self.change("scripts/nettoyer_branches_git_stream.py", "tool\n")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "main")
        self.change("src/cifereca/core/unrelated.py", "main only\n")
        base = git(self.repo, "rev-parse", "HEAD")

        # The old two-tree comparison misleadingly included unrelated Core.
        old = set(git(self.repo, "diff", "--name-only", base, head).splitlines())
        self.assertEqual(old, {
            "scripts/nettoyer_branches_git_stream.py",
            "src/cifereca/core/unrelated.py",
        })
        changed = set(git(self.repo, "diff", "--name-only", f"{base}...{head}").splitlines())
        self.assertEqual(changed, {"scripts/nettoyer_branches_git_stream.py"})
        self.assertEqual(git(self.repo, "diff", "--quiet", base, head, check=False).returncode, 1)

    def test_redundant_pr_tree_detected_even_if_branch_commits_differ(self):
        git(self.repo, "checkout", "-b", "feature")
        self.change("scripts/candidate.py", "same feature\n")
        head = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "checkout", "main")
        self.change("scripts/candidate.py", "same feature\n", message="independent main delivery")
        base = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(base, head)
        # Still present in branch history, but main already has the tree.
        self.assertEqual(
            git(self.repo, "diff", "--name-only", f"{base}...{head}"),
            "scripts/candidate.py",
        )
        self.assertEqual(git(self.repo, "diff", "--quiet", base, head, check=False).returncode, 0)

    def test_readme_scope_is_status_aware_for_pr_changes(self):
        import os
        source = WORKFLOW.read_text(encoding="utf-8")
        full_begin = "          e2e_readme_requires_full() {\n"
        begin = "          e2e_runtime_changes() {\n"
        assert source.count(full_begin) == source.count(begin) == 1
        full_body = source.split(full_begin, 1)[1].split("\n          }\n", 1)[0]
        body = source.split(begin, 1)[1].split("\n          }\n", 1)[0]
        assert 'git diff --name-status --no-renames "$BASE_SHA...$HEAD_SHA"' in full_body
        assert source.count("if e2e_readme_requires_full; then") == 2
        assert 'core_mode=full\n              core_targets=tests' in source
        script = (
            "set -euo pipefail\n"
            'changed_file="$1"\n'
            'e2e_readme_requires_full() {\n' + full_body + "\n}\n"
            'e2e_runtime_changes() {\n' + body + "\n}\n"
            "if e2e_runtime_changes; then echo true; else echo false; fi\n"
        )
        base = git(self.repo, "rev-parse", "HEAD")
        self.change("e2e/README.md", "docs v1\n")
        added = git(self.repo, "rev-parse", "HEAD")

        def selected(before, after, paths):
            changed = self.repo / "changed.txt"
            changed.write_text("\n".join(paths) + "\n")
            result = subprocess.run(
                ["bash", "-c", script, "--", str(changed)],
                cwd=self.repo, capture_output=True, text=True, check=True,
                env={**os.environ, "BASE_SHA": before, "HEAD_SHA": after},
            )
            return result.stdout.strip() == "true"

        self.assertFalse(selected(base, added, ["e2e/README.md"]))
        self.change("e2e/README.md", "docs v2\n")
        modified = git(self.repo, "rev-parse", "HEAD")
        self.assertFalse(selected(added, modified, ["e2e/README.md"]))
        self.change("e2e/probe.py", "VALUE = 1\n")
        mixed = git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(selected(modified, mixed, ["e2e/README.md", "e2e/probe.py"]))
        git(self.repo, "rm", "e2e/README.md")
        git(self.repo, "commit", "-m", "delete README")
        deleted = git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(selected(mixed, deleted, ["e2e/README.md"]))

        self.change("e2e/README.md", "docs v3\n")
        restored = git(self.repo, "rev-parse", "HEAD")
        git(self.repo, "mv", "e2e/README.md", "e2e/README.archived.md")
        git(self.repo, "commit", "-m", "rename README")
        renamed = git(self.repo, "rev-parse", "HEAD")
        self.assertTrue(selected(restored, renamed, [
            "e2e/README.md", "e2e/README.archived.md",
        ]))

    def test_workflow_checks_effective_tree_then_scopes_from_merge_base(self):
        content = WORKFLOW.read_text()
        self.assertIn('git diff --quiet "$BASE_SHA" "$HEAD_SHA"', content)
        self.assertIn('git diff --name-only "$BASE_SHA...$HEAD_SHA" | tee "$changed_file"', content)
        self.assertNotIn('git diff --name-only "$BASE_SHA" "$HEAD_SHA"', content)


if __name__ == "__main__":
    unittest.main()
