import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cifereca.core.library_reconciliation import EvidenceSnapshot
from cifereca.core.library_stored_paths import inspect_stored_paths


class StoredPathsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root/'PRIVATE_OWNER'
        self.library.mkdir()
        (self.library/'PRIVATE_BOOK.epub').write_bytes(b'present')

    def inspect(self, paths, **options):
        snapshot = EvidenceSnapshot({'placements': [dict(identity_id='PRIVATE_OWNER', path=str(p)) for p in paths]})
        return inspect_stored_paths(snapshot, {'PRIVATE_OWNER': (self.library,)}, **options)

    def test_actual_enoent_not_inventory_omission_proves_missing_path(self):
        result = self.inspect([self.library/'PRIVATE_BOOK.epub', self.library/'absent.epub', self.library/'missing-dir'/'absent.epub'])
        self.assertEqual(list(result.states.values()), ['stored_path_present', 'stored_path_missing', 'stored_path_missing'])
        self.assertFalse(result.reasons)
        self.assertNotIn('PRIVATE', json.dumps(result.redacted()))
        self.assertNotIn(str(self.root), json.dumps(result.redacted()))

    def test_missing_root_is_unavailable_not_missing_books(self):
        self.library.rename(self.root/'moved')
        result = self.inspect([self.library/'PRIVATE_BOOK.epub'])
        self.assertEqual(result.states[0], 'stored_path_unavailable')
        self.assertFalse(self.library.exists())

    def test_symlinks_and_specials_and_non_directory_stay_unknown(self):
        (self.library/'link.epub').symlink_to(self.library/'PRIVATE_BOOK.epub')
        (self.library/'alias').symlink_to(self.library, target_is_directory=True)
        os.mkfifo(self.library/'fifo.epub')
        result = self.inspect([self.library/'link.epub', self.library/'alias'/'absent.epub', self.library/'fifo.epub', self.library/'PRIVATE_BOOK.epub'/'child'])
        self.assertEqual(set(result.states.values()), {'stored_path_unavailable'})

    def test_foreign_identity_and_escape_never_probe_filesystem(self):
        paths = [self.root/'foreign.epub', self.library/'..'/'foreign.epub']
        result = self.inspect(paths)
        self.assertEqual(set(result.states.values()), {'stored_path_out_of_scope'})
        snapshot = EvidenceSnapshot({'placements':[dict(identity_id='bob', path=str(self.library/'absent'))]})
        result = inspect_stored_paths(snapshot, {'alice':(self.library,)})
        self.assertEqual(result.states[0], 'stored_path_out_of_scope')

    def test_budget_cancellation_and_incomplete_snapshot(self):
        result = self.inspect([self.library/'a', self.library/'b'], max_paths=1)
        self.assertEqual(len(result.states), 1)
        self.assertIn('stored_path_budget', result.reasons)
        result = self.inspect([self.library/'a'], cancelled=lambda: True)
        self.assertEqual(result.states, {})
        self.assertIn('cancelled', result.reasons)
        snapshot = EvidenceSnapshot(reasons={'snapshot_live_journal'})
        result = inspect_stored_paths(snapshot, {'alice':(self.library,)})
        self.assertIn('snapshot_live_journal', result.reasons)

    def test_access_denied_does_not_become_missing(self):
        original = os.stat
        def deny(path, *args, **kwargs):
            if path == 'denied.epub':
                raise PermissionError('PRIVATE_PATH')
            return original(path, *args, **kwargs)
        with patch('cifereca.core.library_stored_paths.os.stat', side_effect=deny):
            result = self.inspect([self.library/'denied.epub'])
        self.assertEqual(result.states[0], 'stored_path_unavailable')
        self.assertNotIn('PRIVATE', json.dumps(result.redacted()))

    def test_nested_ancestor_replaced_after_absence_is_not_confirmed(self):
        nested = self.library/'nested'
        nested.mkdir()
        original = os.stat
        replaced = False
        def swap(path, *args, **kwargs):
            nonlocal replaced
            if path == 'missing.epub' and not replaced:
                replaced = True
                nested.rename(self.root/'old_nested')
                nested.mkdir()
                (nested/'missing.epub').write_bytes(b'now present')
                raise FileNotFoundError(2, 'private')
            return original(path, *args, **kwargs)
        with patch('cifereca.core.library_stored_paths.os.stat', side_effect=swap):
            result = self.inspect([nested/'missing.epub'])
        self.assertEqual(result.states[0], 'stored_path_unavailable')

    def test_root_replacement_discards_missing_evidence(self):
        from cifereca.core.library_stored_paths import _open_root
        count = 0
        def root(path):
            nonlocal count
            count += 1
            if count == 2:
                self.library.rename(self.root/'moved')
                self.library.mkdir()
            return _open_root(path)
        with patch('cifereca.core.library_stored_paths._open_root', side_effect=root):
            result = self.inspect([self.library/'absent.epub'])
        self.assertEqual(result.states[0], 'stored_path_unavailable')


if __name__ == '__main__':
    unittest.main()
