"""Tests hors réseau du nettoyage progressif des branches."""
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('nettoyer_branches_git_stream.py')
spec = importlib.util.spec_from_file_location('clean_stream', SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def git(*args, cwd):
    return subprocess.run(['git', *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root/'r.git'
        self.repo = root/'repo'
        self.clone = root/'clone'
        git('init', '--bare', '-b', 'main', str(self.remote), cwd=root)
        git('init', '-b', 'main', str(self.repo), cwd=root)
        git('config', 'user.name', 'T', cwd=self.repo)
        git('config', 'user.email', 't@example.com', cwd=self.repo)
        (self.repo/'x').write_text('base')
        git('add', 'x', cwd=self.repo)
        git('commit', '-m', 'base', cwd=self.repo)
        git('remote', 'add', 'origin', str(self.remote), cwd=self.repo)
        git('push', 'origin', 'main', cwd=self.repo)
        self.add_branch('old-integrated', 'a', merge=True)
        self.add_branch('stale-divergent', 'b')
        self.add_branch('open-pr', 'c')
        self.add_branch('referenced-issue', 'd')
        git('clone', str(self.remote), str(self.clone), cwd=root)
        self.exclude = root/'exclude.txt'
        self.exclude.write_text('')
        self.log = root/'log.csv'

    def add_branch(self, name, file, merge=False):
        git('switch', '-c', name, cwd=self.repo)
        (self.repo/file).write_text(name)
        git('add', file, cwd=self.repo)
        git('commit', '-m', name, cwd=self.repo)
        git('push', 'origin', name, cwd=self.repo)
        git('switch', 'main', cwd=self.repo)
        if merge:
            git('merge', '--ff-only', name, cwd=self.repo)
            git('push', 'origin', 'main', cwd=self.repo)

    def simulate(self, *args):
        actual_git = mod.git

        def fake_git(*call, **kw):
            if call == ('remote', 'get-url', 'origin'):
                return subprocess.CompletedProcess([], 0, stdout='https://github.com/pzia/cifereca.git\n')
            return actual_git(*call, **kw)

        def fake_gh(*call):
            if call[:2] == ('pr', 'list'):
                return [{'headRefName': 'open-pr'}]
            if call[:2] == ('issue', 'list') and 'P0' in call:
                return [{'body': 'En cours sur branche referenced-issue'}]
            return []

        old = Path.cwd()
        try:
            os.chdir(self.clone)
            with patch.object(sys, 'argv', [str(SCRIPT), '--exclude', str(self.exclude),
                                            '--log', str(self.log), *args]), \
                 patch.object(mod, 'git', side_effect=fake_git), \
                 patch.object(mod, 'gh', side_effect=fake_gh), \
                 contextlib.redirect_stdout(io.StringIO()):
                return mod.main()
        finally:
            os.chdir(old)

    def remote_names(self):
        return git('ls-remote', '--heads', 'origin', cwd=self.clone)

    def test_r0_only_by_default(self):
        self.assertEqual(self.simulate('--apply'), 0)
        refs = self.remote_names()
        self.assertNotIn('refs/heads/old-integrated', refs)
        for name in ('stale-divergent', 'open-pr', 'referenced-issue'):
            self.assertIn('refs/heads/'+name, refs)

    def test_force_divergent_still_protects_pr_and_active_issue(self):
        self.assertEqual(self.simulate('--apply', '--force-divergent'), 0)
        refs = self.remote_names()
        for name in ('old-integrated', 'stale-divergent'):
            self.assertNotIn('refs/heads/'+name, refs)
        for name in ('main', 'open-pr', 'referenced-issue'):
            self.assertIn('refs/heads/'+name, refs)

    def test_exclusion_and_forced_override(self):
        self.exclude.write_text('stale-divergent\n')
        self.assertEqual(self.simulate('--apply', '--force-divergent'), 0)
        self.assertIn('refs/heads/stale-divergent', self.remote_names())

    def test_forced_directive_without_force_flag(self):
        self.exclude.write_text('!stale-divergent\n')
        self.assertEqual(self.simulate('--apply'), 0)
        self.assertNotIn('refs/heads/stale-divergent', self.remote_names())

    def test_preview_never_deletes(self):
        self.assertEqual(self.simulate('--force-divergent'), 0)
        for name in ('main', 'old-integrated', 'stale-divergent', 'open-pr', 'referenced-issue'):
            self.assertIn('refs/heads/'+name, self.remote_names())


if __name__ == '__main__':
    unittest.main()
