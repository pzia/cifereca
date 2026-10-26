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

    def change(self, path, content):
        file = self.repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        git(self.repo, "add", path)
        git(self.repo, "commit", "-m", path)

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
        self.change("scripts/candidate.py", "same feature\n")
        base = git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(base, head)
        # Still present in branch history, but main already has the tree.
        self.assertEqual(
            git(self.repo, "diff", "--name-only", f"{base}...{head}"),
            "scripts/candidate.py",
        )
        self.assertEqual(git(self.repo, "diff", "--quiet", base, head, check=False).returncode, 0)

    def test_workflow_checks_effective_tree_then_scopes_from_merge_base(self):
        content = WORKFLOW.read_text()
        self.assertIn('git diff --quiet "$BASE_SHA" "$HEAD_SHA"', content)
        self.assertIn('git diff --name-only "$BASE_SHA...$HEAD_SHA" | tee "$changed_file"', content)
        self.assertNotIn('git diff --name-only "$BASE_SHA" "$HEAD_SHA"', content)


if __name__ == "__main__":
    unittest.main()
