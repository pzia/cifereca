import json
import io
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
import unittest

from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.library_reconciliation import EvidenceSnapshot, reconcile, main
from cifereca.core.library_evidence_scope import group_evidence, scope_evidence
from tests.test_library_reconciliation import epub, fixture_db


class ScopeTests(unittest.TestCase):
    def test_foreign_rows_removed_but_conflicting_acquisitions_kept(self):
        right = dict(identity_id='PRIVATE_ALICE', entitlement_id='r', provider_id='numilog', credential_id='c', item_id='item', state='active')
        snapshot = EvidenceSnapshot({
            'entitlements':[right, dict(right, identity_id='PRIVATE_BOB', entitlement_id='rb')],
            'acquisition_ledger':[dict(right, account_id='wrong'), dict(right, identity_id='PRIVATE_BOB'), dict(identity_id='PRIVATE_BOB', entitlement_id='unrelated', credential_id='other')],
            'catalog_entries':[dict(identity_id='PRIVATE_BOB', title='PRIVATE_TITLE')],
            'adept_profile_bindings':[dict(scope_type='identity', scope_id='PRIVATE_ALICE', profile_id='PRIVATE_PROFILE'), dict(scope_type='identity', scope_id='PRIVATE_BOB')],
            'paris_borrower_adept_bindings':[dict(connection_identity_id='PRIVATE_BOB', borrower_identity_id='PRIVATE_BOB')],
        })
        result = scope_evidence(snapshot, ('PRIVATE_ALICE',))
        self.assertEqual(result.tables['entitlements'], [right])
        self.assertEqual(len(result.tables['acquisition_ledger']), 2)
        self.assertFalse(result.tables['catalog_entries'])
        self.assertEqual(len(result.tables['adept_profile_bindings']), 1)
        self.assertFalse(result.tables['paris_borrower_adept_bindings'])

    def test_null_or_blank_right_coordinates_never_import_foreign_acquisitions(self):
        # Legacy half-imported rights do not establish a shared product.
        # Before the fix, (None, None, None) joined every equally incomplete
        # foreign acquisition and exposed its private data in local inspection.
        for empty in (None, '', '   '):
            snapshot = EvidenceSnapshot({
                'entitlements': [
                    dict(identity_id='PRIVATE_ALICE', entitlement_id=empty,
                         provider_id='numilog', credential_id=empty,
                         item_id=empty, state='active'),
                ],
                'acquisition_ledger': [
                    dict(identity_id='PRIVATE_BOB', entitlement_id=empty,
                         provider_id='numilog', credential_id=empty,
                         item_id=empty, account_id='PRIVATE_BORROWER'),
                    dict(identity_id='PRIVATE_BOB', entitlement_id='unrelated',
                         provider_id='numilog', credential_id='c-bob',
                         item_id='item-bob', account_id='PRIVATE_OTHER'),
                ],
            })
            scoped = scope_evidence(snapshot, ('PRIVATE_ALICE',))
            self.assertEqual(len(scoped.tables['entitlements']), 1)
            self.assertEqual(scoped.tables['acquisition_ledger'], [])
            self.assertNotIn('PRIVATE_BORROWER', repr(scoped.tables))
            self.assertNotIn('PRIVATE_OTHER', repr(scoped.tables))

    def test_complete_selected_provenance_keeps_foreign_conflict_evidence(self):
        # Shared exact entitlement or provider/credential/item is genuine
        # conflicting-owner evidence and must not be hidden by this guard.
        right = dict(identity_id='PRIVATE_ALICE', entitlement_id='right-a',
                     provider_id='numilog', credential_id='cred-a',
                     item_id='item-a', state='active')
        foreign_same_entitlement = dict(identity_id='PRIVATE_BOB',
                                        entitlement_id='right-a',
                                        provider_id='another',
                                        credential_id='cred-b', item_id='item-b')
        foreign_same_source = dict(identity_id='PRIVATE_BOB',
                                   entitlement_id='right-b',
                                   provider_id='numilog', credential_id='cred-a',
                                   item_id='item-a')
        scoped = scope_evidence(EvidenceSnapshot({
            'entitlements': [right],
            'acquisition_ledger': [
                foreign_same_entitlement, foreign_same_source,
                dict(identity_id='PRIVATE_BOB', entitlement_id=None,
                     provider_id=None, credential_id=None, item_id=None),
            ],
        }), ('PRIVATE_ALICE',))
        self.assertEqual(scoped.tables['acquisition_ledger'], [
            foreign_same_entitlement, foreign_same_source,
        ])

    def test_no_provider_inference_or_private_identifiers_in_groups(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            epub(root/'PRIVATE_BOOK.epub')
            inventory = scan_identity_roots({'PRIVATE_ALICE': (root,)})
            file = inventory.files[0]
            snapshot = EvidenceSnapshot({'artifacts':[dict(artifact_id='sha256:'+file.sha256, sha256=file.sha256, size=file.size)],
                                         'entitlements':[dict(identity_id='PRIVATE_ALICE', state='active', provider_id='PRIVATE_HOSTILE', item_id='item')]})
            report = reconcile(inventory, snapshot)
            groups = group_evidence(inventory, snapshot, report, ('PRIVATE_ALICE',))
            output = json.dumps(groups.redacted())
            self.assertNotIn('PRIVATE', output)
            self.assertNotIn(file.sha256, output)
            self.assertIn('unattributed', output)
            self.assertNotIn('numilog', output)
            self.assertEqual(groups.redacted()['adept_status'], 'not_requested')


    def test_cli_scopes_private_rows_and_only_counts_local_adept_references(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root/'PRIVATE_ALICE'
            library.mkdir()
            epub(library/'PRIVATE.epub')
            database = root/'db.sqlite'
            fixture_db(database)
            with sqlite3.connect(database) as db:
                db.execute("INSERT INTO entitlements (identity_id,state,item_id,provider_id,metadata_json) VALUES ('PRIVATE_BOB','active','item','numilog','{}')")
                db.executemany('INSERT INTO adept_profile_bindings VALUES (?,?,?)', [('identity','PRIVATE_ALICE','PRIVATE_PROFILE_A'), ('identity','PRIVATE_BOB','PRIVATE_PROFILE_B')])
            output = io.StringIO()
            report_path = root/'private.json'
            with redirect_stdout(output):
                code = main(['--database',str(database),'--root','PRIVATE_ALICE='+str(library),'--private-report',str(report_path)])
            self.assertEqual(code, 0)
            public = json.loads(output.getvalue())
            self.assertNotIn('PRIVATE', output.getvalue())
            self.assertEqual(public['groups']['adept_status'], 'local_reference_only')
            self.assertEqual(public['groups']['adept_local_references'], 1)
            private = json.loads(report_path.read_text())
            self.assertFalse(private['snapshot']['tables']['entitlements'])
            self.assertNotIn('PRIVATE_BOB', report_path.read_text())
            self.assertNotIn('PRIVATE_PROFILE_B', report_path.read_text())

if __name__ == '__main__':
    unittest.main()
