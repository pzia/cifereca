"""Synthetic offline invariants: no source writes, cross-account refusal, budgets."""
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import zipfile

from cifereca import epub_metadata
from cifereca.core.library_inventory import InventoryLimits, scan_identity_roots
from cifereca.core.library_reconciliation import EvidenceSnapshot, _COLUMNS, read_quiescent_snapshot, reconcile, main


def epub(path, title='PRIVATE_TITLE', identifier='9780000000000', xml=None):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('mimetype', b'application/epub+zip')
        z.writestr('META-INF/container.xml', "<container><rootfile full-path='content.opf'/></container>")
        z.writestr('content.opf', xml or (
            "<package><metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            f'<dc:title>{title}</dc:title><dc:creator>PRIVATE_AUTHOR</dc:creator>'
            f'<dc:identifier>{identifier}</dc:identifier></metadata>'
            "<manifest><item id='c' href='cover.png' properties='cover-image'/></manifest></package>"))
        z.writestr('cover.png', b'not needed')


def filesystem(path):
    return {str(p.relative_to(path)): (p.lstat().st_size, p.lstat().st_mtime_ns,
            hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() and not p.is_symlink() else None)
            for p in path.rglob('*')}


def fixture_db(path):
    with sqlite3.connect(path) as db:
        for name, columns in _COLUMNS.items():
            db.execute(f"CREATE TABLE {name} ({','.join(c + (' INTEGER' if c == 'size' else ' TEXT') for c in columns.split())})")


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root/'PRIVATE_IDENTITY'
        self.library.mkdir()
        self.book = self.library/'PRIVATE_BOOK.epub'
        epub(self.book)

    def scan(self, **kw):
        return scan_identity_roots({'PRIVATE_IDENTITY': (self.library,)}, **kw)

    def test_no_cover_cache_or_any_source_write(self):
        before = filesystem(self.root)
        with patch.object(epub_metadata, '_publish_cover', side_effect=AssertionError('cover write')), patch.object(epub_metadata, 'cover_cache_dir', side_effect=AssertionError('cache write')):
            result = self.scan()
        self.assertEqual(filesystem(self.root), before)
        f = result.files[0]
        self.assertEqual(f.metadata['title'], 'PRIVATE_TITLE')
        self.assertEqual(f.sha256, hashlib.sha256(self.book.read_bytes()).hexdigest())
        self.assertEqual(f.validation, 'ok')
        self.assertNotIn('PRIVATE', json.dumps(result.redacted()))
        self.assertNotIn(f.sha256, json.dumps(result.redacted()))
        self.assertNotIn('PRIVATE', repr(result))

    def test_metadata_only_accepts_stream_and_does_not_close_it(self):
        with self.book.open('rb') as stream:
            result = epub_metadata.inspect_epub(stream, metadata_only=True)
            self.assertFalse(stream.closed)
        self.assertEqual(result['epub_status'], 'ok')
        self.assertEqual(result['epub_cover_status'], 'not_requested')

    def test_utf16_dtd_and_entities_rejected(self):
        payload = '<!DOCTYPE x [<!ENTITY x "SECRET">]><package><metadata>&x;</metadata></package>'
        for encoding in ('utf-8', 'utf-16', 'utf-32'):
            epub(self.book, xml=payload.encode(encoding))
            self.assertEqual(self.scan().files[0].validation, 'unsafe_xml')

    def test_symlinks_and_special_files_never_opened(self):
        (self.library/'outside.epub').symlink_to(self.book)
        (self.library/'loop').symlink_to(self.library, target_is_directory=True)
        os.mkfifo(self.library/'fifo.epub')
        result = self.scan()
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.skipped_symlinks, 2)
        self.assertEqual(result.skipped_special, 1)

    def test_symlink_root_and_symlink_ancestor_refused(self):
        alias = self.root/'alias'
        alias.symlink_to(self.library, target_is_directory=True)
        for root in (alias, alias/'nested'):
            result = scan_identity_roots({'i': (root,)})
            self.assertEqual(result.files, [])
            self.assertIn('directory_unavailable', result.reasons)

    def test_missing_root_is_incomplete_not_empty_proof(self):
        result = scan_identity_roots({'i': (self.root/'absent',)})
        self.assertEqual(result.redacted()['status'], 'incomplete_scan')
        self.assertEqual(len(result.unavailable_directories), 1)
        self.assertFalse((self.root/'absent').exists())

    def test_overlap_rejected_even_between_identities(self):
        with self.assertRaisesRegex(ValueError, 'overlapping_inventory_roots'):
            scan_identity_roots({'a': (self.library,), 'b': (self.library/'child',)})

    def test_root_cap_is_bounded_before_traversal(self):
        with self.assertRaisesRegex(ValueError, 'invalid_inventory_roots'):
            scan_identity_roots({str(i): (self.root/str(i),) for i in range(129)})

    def test_recursive_and_depth_budget(self):
        nested = self.library/'a'/'b'
        nested.mkdir(parents=True)
        epub(nested/'book.epub')
        self.assertEqual(len(self.scan().files), 2)
        result = self.scan(limits=InventoryLimits(depth=1))
        self.assertIn('depth_budget', result.reasons)

    def test_hash_and_file_budgets_are_explicit(self):
        result = self.scan(limits=InventoryLimits(hash_bytes=1))
        self.assertIsNone(result.files[0].sha256)
        self.assertIn('hash_byte_budget', result.reasons)
        result = self.scan(limits=InventoryLimits(file_bytes=1))
        self.assertIn('file_byte_budget', result.reasons)

    def test_cumulative_metadata_budget_does_not_drop_byte_observations(self):
        result = self.scan(limits=InventoryLimits(metadata_bytes=1))
        self.assertIn('metadata_byte_budget', result.reasons)
        self.assertEqual(result.metadata_bytes, 0)
        self.assertEqual(result.files[0].metadata, {})
        self.assertIsNotNone(result.files[0].sha256)

    def test_entry_file_and_cancel_limits(self):
        epub(self.library/'another.epub')
        self.assertIn('entry_budget', self.scan(limits=InventoryLimits(entries=1)).reasons)
        self.assertIn('file_budget', self.scan(limits=InventoryLimits(files=1)).reasons)
        self.assertEqual(self.scan(cancelled=lambda: True).files, [])
        self.assertIn('cancelled', self.scan(cancelled=lambda: True).reasons)

    def test_time_limit(self):
        with patch('cifereca.core.library_inventory.time.monotonic', side_effect=[0, 200]):
            result = self.scan()
        self.assertIn('time_budget', result.reasons)

    def test_replaced_file_after_hash_is_rejected(self):
        original = epub_metadata.inspect_epub
        def replace(stream, **kwargs):
            result = original(stream, **kwargs)
            self.book.unlink()
            epub(self.book, title='REPLACED')
            return result
        with patch('cifereca.core.library_inventory.inspect_epub', side_effect=replace):
            result = self.scan()
        self.assertEqual(result.files, [])
        self.assertIn('file_changed', result.reasons)

    def test_root_replaced_during_walk_drops_former_identity_observations(self):
        original = epub_metadata.inspect_epub
        moved = self.root/'moved'
        def replace(stream, **kwargs):
            result = original(stream, **kwargs)
            self.library.rename(moved)
            self.library.mkdir()
            return result
        with patch('cifereca.core.library_inventory.inspect_epub', side_effect=replace):
            result = self.scan()
        self.assertFalse(result.files)
        self.assertIn('root_changed', result.reasons)

    def test_nested_directory_replaced_during_walk_is_rejected(self):
        nested = self.library/'nested'
        nested.mkdir()
        epub(nested/'nested.epub')
        original = epub_metadata.inspect_epub
        inode = (nested/'nested.epub').stat().st_ino
        def replace(stream, **kwargs):
            result = original(stream, **kwargs)
            if os.fstat(stream.fileno()).st_ino == inode:
                nested.rename(self.root/'moved')
                nested.mkdir()
            return result
        with patch('cifereca.core.library_inventory.inspect_epub', side_effect=replace):
            result = self.scan()
        self.assertIn('directory_changed', result.reasons)
        self.assertFalse(any('/nested/' in f.path for f in result.files))

    def test_corrupt_epub_and_pdf_signature_are_not_valid_epub_proof(self):
        self.book.write_bytes(b'broken')
        (self.library/'sample.pdf').write_bytes(b'%PDF-1.4\nnot a validated PDF')
        result = self.scan()
        self.assertEqual({f.validation for f in result.files}, {'unreadable_metadata', 'pdf_signature_only'})

    def test_zip_entry_ceiling_checked_before_zipfile_allocates(self):
        raw = bytearray(self.book.read_bytes())
        offset = raw.rfind(b'PK\x05\x06')
        struct.pack_into('<HH', raw, offset+8, 11000, 11000)
        stream = io.BytesIO(raw)
        with patch.object(epub_metadata.zipfile, 'ZipFile', side_effect=AssertionError('allocation')):
            result = epub_metadata.inspect_epub(stream, metadata_only=True)
        self.assertEqual(result['epub_status'], 'too_many_entries')

    def test_progress_statistics_revoke_replaced_root_without_retaining_files(self):
        from cifereca.core import library_inventory as inventory

        alternate = self.root/'PRIVATE_SECOND_ROOT'
        alternate.mkdir()
        epub(alternate/'SECOND.epub', title='PRIVATE_SECOND')
        moved = self.root/'PRIVATE_RENAMED_ROOT'
        inode = self.book.stat().st_ino
        original = inventory.inspect_epub
        replaced = [False]

        def replace_first_root(stream, **kwargs):
            data = original(stream, **kwargs)
            if not replaced[0] and os.fstat(stream.fileno()).st_ino == inode:
                self.library.rename(moved)
                self.library.mkdir()
                replaced[0] = True
            return data

        progress = []
        with patch.object(inventory, 'inspect_epub', side_effect=replace_first_root):
            result = scan_identity_roots(
                {'PRIVATE_IDENTITY': (self.library,),
                 'PRIVATE_SECOND_IDENTITY': (alternate,)},
                progress=progress.append,
            )
        self.assertTrue(replaced[0])
        self.assertIn('root_changed', result.reasons)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0].identity_id, 'PRIVATE_SECOND_IDENTITY')
        self.assertEqual(progress[-1]['files'], 1)
        self.assertEqual(progress[-1]['hashed_files'], 1)
        self.assertEqual(progress[-1]['validation'], {'ok': 1})
        self.assertEqual(progress[-1], result.redacted())
        self.assertNotIn('PRIVATE', json.dumps(progress))

    def test_more_than_522_books_progress_and_no_writes(self):
        raw = self.book.read_bytes()
        for i in range(523):
            (self.library/f'{i}.epub').write_bytes(raw)
        before = filesystem(self.root)
        progress = []
        result = self.scan(progress=progress.append)
        self.assertEqual(len(result.files), 524)
        self.assertEqual(len(progress), 524)
        # Regression: live redacted counters are exactly equivalent to
        # re-reading the retained observations, without quadratic rescans.
        for index, sample in enumerate(progress, 1):
            self.assertEqual(sample['files'], index)
            self.assertEqual(sample['hashed_files'], index)
            self.assertEqual(sample['validation'], {'ok': index})
            self.assertEqual(sample['structure_validation'],
                             {'epub_structure_crc_ok': index})
            self.assertNotIn('PRIVATE', json.dumps(sample))
        self.assertEqual(progress[-1], result.redacted())
        self.assertFalse(result.reasons)
        self.assertEqual(before, filesystem(self.root))


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root/'fixture.sqlite'
        fixture_db(self.path)

    def test_stable_snapshot_has_no_sqlite_side_effects(self):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO artifacts VALUES ('a','private_sha',123,'epub')")
        before = filesystem(self.root)
        result = read_quiescent_snapshot(self.path)
        self.assertFalse(result.reasons)
        self.assertEqual(result.tables['artifacts'][0]['size'], 123)
        self.assertEqual(before, filesystem(self.root))

    def test_wal_live_db_refused_without_shm_creation_or_checkpoint(self):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO artifacts VALUES ('a','hash',123,'epub')")
        db.commit()
        before = filesystem(self.root)
        result = read_quiescent_snapshot(self.path)
        self.assertEqual(result.reasons, {'snapshot_live_journal'})
        self.assertFalse(result.tables)
        self.assertEqual(before, filesystem(self.root))

    def test_closed_wal_without_sidecars_is_supported(self):
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
        db.close()
        before = filesystem(self.root)
        result = read_quiescent_snapshot(self.path)
        self.assertFalse(result.reasons)
        self.assertEqual(before, filesystem(self.root))

    def test_row_and_byte_budgets(self):
        with sqlite3.connect(self.path) as db:
            db.executemany('INSERT INTO artifacts VALUES (?,?,?,?)', [('a','h',1,'epub'), ('b','x',2,'epub')])
        result = read_quiescent_snapshot(self.path, max_rows=1)
        self.assertIn('snapshot_row_budget', result.reasons)
        self.assertEqual(len(result.tables['artifacts']), 1)
        self.assertIn('snapshot_byte_budget_or_invalid', read_quiescent_snapshot(self.path, max_bytes=100).reasons)

    def test_snapshot_cancelled_and_database_replaced_during_copy(self):
        self.assertEqual(read_quiescent_snapshot(self.path, cancelled=lambda: True).reasons, {'snapshot_cancelled'})
        original = os.read
        def read(fd, size):
            value = original(fd, size)
            self.path.rename(self.root/'moved.sqlite')
            fixture_db(self.path)
            return value
        with patch('cifereca.core.library_reconciliation.os.read', side_effect=read):
            result = read_quiescent_snapshot(self.path)
        self.assertEqual(result.reasons, {'snapshot_changed'})
        self.assertFalse(result.tables)

    def test_real_current_storage_schema_snapshot_and_metadata_projection(self):
        from cifereca.storage import Storage
        storage = Storage(self.root/'actual.sqlite')
        storage.entitlement_put(dict(entitlement_id='r', provider_id='numilog', credential_id='c',
                                    identity_id='i', account_id='a', item_id='item',
                                    metadata={'source':'numilog.authenticated_library', 'token':'SECRET'}))
        storage.close()
        before = filesystem(self.root)
        result = read_quiescent_snapshot(self.root/'actual.sqlite')
        self.assertFalse(result.reasons)
        right = result.tables['entitlements'][0]
        self.assertEqual(right['attested_source'], 'numilog.authenticated_library')
        self.assertNotIn('SECRET', repr(right))
        self.assertEqual(before, filesystem(self.root))

    def test_missing_and_symlink_db_never_created_or_opened(self):
        absent = self.root/'absent.sqlite'
        self.assertIn('snapshot_unavailable', read_quiescent_snapshot(absent).reasons)
        self.assertFalse(absent.exists())
        alias = self.root/'alias.sqlite'
        alias.symlink_to(self.path)
        self.assertIn('snapshot_unavailable', read_quiescent_snapshot(alias).reasons)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.book = self.root/'PRIVATE.epub'
        epub(self.book)
        self.inventory = scan_identity_roots({'alice': (self.root,)})
        f = self.inventory.files[0]
        self.artifact = 'sha256:'+f.sha256
        self.right = dict(entitlement_id='r', provider_id='numilog', credential_id='c', account_id='account', item_id='item', identity_id='alice', state='active', entitlement_type='owned', attested_source='numilog.authenticated_library')
        self.ledger = dict(self.right, acquisition_id='l', artifact_id=self.artifact, status='complete')
        self.placement = dict(placement_id='p', artifact_id=self.artifact, path=str(self.book), identity_id='alice', provider_id='numilog', source_item_id='item', state='present')
        self.snapshot = EvidenceSnapshot({'entitlements':[self.right], 'acquisition_ledger':[self.ledger], 'artifacts':[dict(artifact_id=self.artifact, sha256=f.sha256, size=f.size, media_format='epub')], 'placements':[self.placement]})

    def test_full_explicit_chain_confirms_link_without_any_action_authority(self):
        report = reconcile(self.inventory, self.snapshot)
        self.assertEqual(report.files[0], {'confirmed_link'})
        self.assertEqual(report.unverified_rights, [])
        self.assertFalse(report.redacted()['acquisition_authorized'])
        self.assertNotIn('PRIVATE', json.dumps(report.redacted()))
        self.assertNotIn(self.inventory.files[0].sha256, json.dumps(report.redacted()))

    def test_identical_bytes_and_isbn_do_not_prove_account_or_right(self):
        self.placement['identity_id'] = 'bob'
        report = reconcile(self.inventory, self.snapshot)
        self.assertEqual(report.files[0], {'present_unlinked', 'local_file_without_attested_right'})
        self.assertEqual(report.unverified_rights, [0])

    def test_missing_ledger_or_incomplete_or_wrong_account_never_confirm(self):
        for key, value in [('account_id', 'other'), ('identity_id', 'bob'), ('status', 'acquiring'), ('entitlement_id', 'other')]:
            with self.subTest(key=key):
                old = self.ledger[key]
                self.ledger[key] = value
                self.assertNotIn('confirmed_link', reconcile(self.inventory, self.snapshot).files[0])
                self.ledger[key] = old
        self.snapshot.tables['acquisition_ledger'] = []
        self.assertNotIn('confirmed_link', reconcile(self.inventory, self.snapshot).files[0])

    def test_mismatch_and_conflicting_ledger_are_conservative(self):
        self.snapshot.tables['artifacts'][0]['sha256'] = '0'*64
        self.assertIn('hash_mismatch', reconcile(self.inventory, self.snapshot).files[0])
        self.snapshot.tables['artifacts'][0]['sha256'] = self.inventory.files[0].sha256
        self.snapshot.tables['acquisition_ledger'].append(dict(self.ledger, account_id='other'))
        self.assertNotIn('confirmed_link', reconcile(self.inventory, self.snapshot).files[0])

    def test_readable_opf_but_bad_member_crc_blocks_link_confirmation(self):
        raw = self.book.read_bytes().replace(b'not needed', b'corruption')
        self.book.write_bytes(raw)
        self.inventory = scan_identity_roots({'alice': (self.root,)})
        observation = self.inventory.files[0]
        self.assertEqual(observation.validation, 'ok')
        self.assertNotEqual(observation.structure_validation, 'epub_structure_crc_ok')
        # Even a persisted SHA of these exact damaged bytes is insufficient.
        artifact = 'sha256:'+observation.sha256
        self.snapshot.tables['artifacts'][0].update(artifact_id=artifact, sha256=observation.sha256, size=observation.size)
        self.ledger['artifact_id'] = self.placement['artifact_id'] = artifact
        self.assertNotIn('confirmed_link', reconcile(self.inventory, self.snapshot).files[0])

    def test_unattested_or_expiring_or_loan_right_cannot_confirm(self):
        for key, value in [('attested_source', None), ('entitlement_type', 'loan'), ('loan_id', 'loan'), ('expires_at', '2099-01-01')]:
            with self.subTest(key=key):
                old = self.right.get(key)
                self.right[key] = value
                self.assertNotIn('confirmed_link', reconcile(self.inventory, self.snapshot).files[0])
                self.right[key] = old

    def test_two_attested_rights_same_account_are_not_one_confirmed_link(self):
        # Two purchases/entitlements may share account, item and bytes.
        # The graph cannot silently merge two distinct rights into one
        # unambiguous rights-to-file link merely because account IDs match.
        second = dict(self.right, entitlement_id='r2')
        second_ledger = dict(self.ledger, acquisition_id='l2', entitlement_id='r2')
        self.snapshot.tables['entitlements'].append(second)
        self.snapshot.tables['acquisition_ledger'].append(second_ledger)
        report = reconcile(self.inventory, self.snapshot)
        self.assertIn('ambiguous', report.files[0])
        self.assertNotIn('confirmed_link', report.files[0])
        self.assertEqual(report.unverified_rights, [0, 1])
        self.assertFalse(report.redacted()['acquisition_authorized'])
        self.assertNotIn('PRIVATE', json.dumps(report.redacted()))

    def test_duplicate_attested_entitlement_id_is_not_a_unique_right(self):
        # A malformed/duplicated snapshot must not be promoted to proof
        # even when both rows have identical account, entitlement and ledger.
        self.snapshot.tables['entitlements'].append(dict(self.right))
        report = reconcile(self.inventory, self.snapshot)
        self.assertIn('ambiguous', report.files[0])
        self.assertNotIn('confirmed_link', report.files[0])
        self.assertEqual(report.unverified_rights, [0, 1])

    def test_duplicate_accounts_for_same_placement_remain_ambiguous(self):
        self.snapshot.tables['entitlements'].append(dict(self.right, entitlement_id='r2', account_id='a2', credential_id='c2'))
        self.snapshot.tables['acquisition_ledger'].append(dict(self.ledger, entitlement_id='r2', account_id='a2', credential_id='c2'))
        report = reconcile(self.inventory, self.snapshot)
        self.assertIn('ambiguous', report.files[0])
        self.assertNotIn('confirmed_link', report.files[0])
        self.assertEqual(report.unverified_rights, [0, 1])

    def test_backup_sha_discovers_unlinked_candidate_but_never_attests_right(self):
        self.snapshot.tables['artifacts'] = []
        self.snapshot.tables['placements'] = []
        f = self.inventory.files[0]
        self.snapshot.tables['backup_items'] = [dict(identity_id='alice', sha256=f.sha256, size=f.size)]
        report = reconcile(self.inventory, self.snapshot)
        self.assertIn('present_unlinked', report.files[0])
        self.assertEqual(report.edges[0].certainty, 'bytes')
        self.assertNotIn('confirmed_link', report.files[0])

    def test_incomplete_snapshot_never_confirms_and_never_claims_missing_file(self):
        self.snapshot.reasons.add('snapshot_row_budget')
        report = reconcile(self.inventory, self.snapshot)
        self.assertNotIn('confirmed_link', report.files[0])
        self.assertEqual(report.missing_placements, [])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.library = self.root/'PRIVATE_IDENTITY'
        self.library.mkdir()
        epub(self.library/'PRIVATE_BOOK.epub')
        self.path = self.root/'PRIVATE_DB.sqlite'
        fixture_db(self.path)
        self.args = ['--database', str(self.path), '--root', 'PRIVATE_IDENTITY='+str(self.library)]

    def run_cli(self, extra=()):
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(self.args+list(extra))
        return code, output.getvalue()

    def test_default_output_is_redacted_and_creates_nothing(self):
        before = filesystem(self.root)
        code, text = self.run_cli()
        self.assertEqual(code, 0)
        self.assertNotIn('PRIVATE', text)
        self.assertEqual(before, filesystem(self.root))

    def test_private_export_is_explicit_new_and_never_overwrites(self):
        destination = self.root/'private.json'
        code, text = self.run_cli(['--private-report', str(destination)])
        self.assertEqual(code, 0)
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        before = destination.read_bytes()
        self.assertIn('PRIVATE', before.decode())
        self.assertNotIn('PRIVATE', text)
        code, text = self.run_cli(['--private-report', str(destination)])
        self.assertEqual(code, 2)
        self.assertEqual(destination.read_bytes(), before)
        self.assertNotIn('PRIVATE', text)

    def test_invalid_or_missing_root_does_not_leak_paths(self):
        self.args[-1] = 'PRIVATE_IDENTITY='+str(self.root/'absent')
        code, text = self.run_cli()
        self.assertEqual(code, 2)
        self.assertNotIn('PRIVATE', text)
        self.assertNotIn(str(self.root), text)


if __name__ == '__main__':
    unittest.main()
