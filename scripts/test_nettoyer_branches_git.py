"""Tests hors réseau d'une simulation et de la suppression contrôlée de branches."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('nettoyer_branches_git.py')
spec = importlib.util.spec_from_file_location('clean_branches', SCRIPT)
assert spec and spec.loader
clean = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = clean
spec.loader.exec_module(clean)


def run(*args, cwd: Path) -> str:
    completed = subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


class BranchCleanerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.remote = base / 'origin.git'
        self.repo = base / 'builder'
        self.checkout = base / 'checkout'
        run('init', '--bare', '-b', 'main', str(self.remote), cwd=base)
        run('init', '-b', 'main', str(self.repo), cwd=base)
        run('config', 'user.email', 'test@example.com', cwd=self.repo)
        run('config', 'user.name', 'Test', cwd=self.repo)
        (self.repo / 'base').write_text('init')
        run('add', 'base', cwd=self.repo)
        run('commit', '-m', 'initial', cwd=self.repo)
        run('remote', 'add', 'origin', str(self.remote), cwd=self.repo)
        run('push', '--set-upstream', 'origin', 'main', cwd=self.repo)
        run('switch', '-c', 'feature/r0', cwd=self.repo)
        (self.repo / 'feature0').write_text('r0')
        run('add', 'feature0', cwd=self.repo)
        run('commit', '-m', 'feature r0', cwd=self.repo)
        run('push', 'origin', 'feature/r0', cwd=self.repo)
        run('switch', 'main', cwd=self.repo)
        run('merge', '--ff-only', 'feature/r0', cwd=self.repo)
        run('push', 'origin', 'main', cwd=self.repo)
        run('switch', '-c', 'feature/r1', cwd=self.repo)
        (self.repo / 'feature1').write_text('squash')
        run('add', 'feature1', cwd=self.repo)
        run('commit', '-m', 'r1 original', cwd=self.repo)
        run('push', 'origin', 'feature/r1', cwd=self.repo)
        run('switch', 'main', cwd=self.repo)
        run('merge', '--squash', 'feature/r1', cwd=self.repo)
        run('commit', '-m', 'feat: squash (#123)', cwd=self.repo)
        run('push', 'origin', 'main', cwd=self.repo)
        run('switch', '-c', 'feature/keep', cwd=self.repo)
        (self.repo / 'feature2').write_text('not merged')
        run('add', 'feature2', cwd=self.repo)
        run('commit', '-m', 'unmerged', cwd=self.repo)
        run('push', 'origin', 'feature/keep', cwd=self.repo)
        run('switch', 'main', cwd=self.repo)
        run('branch', 'feature/open', cwd=self.repo)
        run('push', 'origin', 'feature/open', cwd=self.repo)
        run('clone', '--single-branch', '--branch', 'main', str(self.remote), str(self.checkout), cwd=base)
        self.open_pr_path = base / 'open_prs.txt'
        self.open_pr_path.write_text('feature/open\n')
        self.plan_path = base / 'plan.json'
        self.report_path = base / 'report.csv'

    def execute(self, *args: str) -> tuple[int, str]:
        original_git = clean.git

        def replacement(*a: str, **kw):
            if a == ('remote', 'get-url', 'origin'):
                return subprocess.CompletedProcess(['git', *a], 0,
                                                   stdout='https://github.com/pzia/cifereca.git\n')
            return original_git(*a, **kw)

        previous_cwd = Path.cwd()
        stdout = io.StringIO()
        try:
            os.chdir(self.checkout)
            with patch.object(sys, 'argv', [str(SCRIPT), *args]), \
                 patch.object(clean, 'git', side_effect=replacement), \
                 contextlib.redirect_stdout(stdout):
                rc = clean.main()
        finally:
            os.chdir(previous_cwd)
        return rc, stdout.getvalue()

    def dryrun(self) -> dict:
        rc, _ = self.execute('--open-pr-branches', str(self.open_pr_path),
                             '--plan', str(self.plan_path), '--report', str(self.report_path),
                             '--list-limit', '0')
        self.assertEqual(rc, 0)
        return json.loads(self.plan_path.read_text())

    def test_simulation_protects_open_pr_and_classifies_r0_r1(self):
        plan = self.dryrun()
        categories = {c['branch']: c['category'] for c in plan['candidates']}
        self.assertEqual(categories, {'feature/r0': 'R0', 'feature/r1': 'R1-git'})
        self.assertEqual(plan['open_pr_branches'], ['feature/open'])
        self.assertIn('feature/keep', self.report_path.read_text())
        self.assertIn('feature/open', self.report_path.read_text())
        self.assertEqual(run('ls-remote', '--heads', 'origin', cwd=self.checkout).count('\n'), 4)

    def test_apply_requires_plan_snapshot_and_explicit_cap(self):
        with self.assertRaises(SystemExit) as cm:
            self.execute('--apply', '--yes')
        self.assertEqual(cm.exception.code, 2)

    def test_apply_rejects_modified_plan_and_does_not_delete(self):
        plan = self.dryrun()
        plan['main_sha'] = '0' * 40
        self.plan_path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(RuntimeError, 'Le plan ne correspond'):
            self.execute('--apply', '--yes', '--max-delete', '2',
                         '--open-pr-branches', str(self.open_pr_path),
                         '--plan', str(self.plan_path))
        self.assertTrue(run('ls-remote', '--heads', 'origin', 'feature/r0', cwd=self.checkout))

    def test_apply_refuses_when_remote_head_changed_since_simulation(self):
        self.dryrun()
        run('switch', 'feature/r0', cwd=self.repo)
        (self.repo / 'extra').write_text('new work')
        run('add', 'extra', cwd=self.repo)
        run('commit', '-m', 'new work', cwd=self.repo)
        run('push', 'origin', 'feature/r0', cwd=self.repo)
        with self.assertRaisesRegex(RuntimeError, 'Le plan ne correspond'):
            self.execute('--apply', '--yes', '--max-delete', '2',
                         '--open-pr-branches', str(self.open_pr_path),
                         '--plan', str(self.plan_path))
        self.assertTrue(run('ls-remote', '--heads', 'origin', 'feature/r1', cwd=self.checkout))

    def test_apply_rejects_candidate_count_above_cap(self):
        self.dryrun()
        with self.assertRaisesRegex(RuntimeError, '2 candidates > --max-delete 1'):
            self.execute('--apply', '--yes', '--max-delete', '1',
                         '--open-pr-branches', str(self.open_pr_path),
                         '--plan', str(self.plan_path))
        self.assertTrue(run('ls-remote', '--heads', 'origin', 'feature/r0', cwd=self.checkout))

    def test_r1_manifest_must_match_current_head(self):
        self.dryrun()
        proof = {
            'branch': 'feature/keep', 'merged': True, 'base': 'main',
            'head_repo_full_name': 'pzia/cifereca', 'pr_number': 456,
            'pr_head_sha': 'f' * 40,
            'merge_commit_sha': run('rev-parse', 'main', cwd=self.repo),
        }
        manifest = self.checkout.parent / 'merged.json'
        manifest.write_text(json.dumps({'merged_pull_requests': [proof]}))
        rc, _ = self.execute('--r1-manifest', str(manifest), '--skip-r1-git',
                             '--open-pr-branches', str(self.open_pr_path))
        self.assertEqual(rc, 0)
        self.assertTrue(run('ls-remote', '--heads', 'origin', 'feature/keep', cwd=self.checkout))

    def test_apply_deletes_only_approved_candidates(self):
        self.dryrun()
        rc, output = self.execute('--apply', '--yes', '--max-delete', '2',
                                  '--open-pr-branches', str(self.open_pr_path),
                                  '--plan', str(self.plan_path), '--list-limit', '0')
        self.assertEqual(rc, 0, output)
        remote_refs = run('ls-remote', '--heads', 'origin', cwd=self.checkout)
        self.assertNotIn('refs/heads/feature/r0', remote_refs)
        self.assertNotIn('refs/heads/feature/r1', remote_refs)
        self.assertIn('refs/heads/feature/keep', remote_refs)
        self.assertIn('refs/heads/feature/open', remote_refs)


if __name__ == '__main__':
    unittest.main()
