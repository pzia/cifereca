import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from cifereca.core.library_metadata_candidates import identifiers, metadata_candidates
from cifereca.core.library_inventory import scan_identity_roots
from cifereca.core.library_reconciliation import EvidenceSnapshot, read_quiescent_snapshot, reconcile
from tests.test_library_reconciliation import epub, fixture_db


class MetadataCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        epub(self.root/'PRIVATE.epub', identifier='urn:isbn:9780306406157')
        self.inventory = scan_identity_roots({'alice':(self.root,)})
        self.snapshot = EvidenceSnapshot({'catalog_entries': [dict(identity_id='alice', bibliographic_identifiers=[('isbn13','9780306406157')])]})

    def test_exact_identifier_is_only_same_identity_metadata_candidate(self):
        result = metadata_candidates(self.inventory, self.snapshot)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].certainty, 'metadata_only')
        self.assertFalse(result.redacted()['acquisition_authorized'])
        self.assertNotIn('9780306406157', json.dumps(result.redacted()))
        self.assertNotIn('confirmed_link', reconcile(self.inventory, self.snapshot).files[0])
        self.snapshot.tables['catalog_entries'][0]['identity_id'] = 'bob'
        self.assertFalse(metadata_candidates(self.inventory, self.snapshot).candidates)

    def test_no_fuzzy_title_author_filename_or_similar_isbn_match(self):
        row = self.snapshot.tables['catalog_entries'][0]
        row['bibliographic_identifiers'] = [('isbn13','9780306406164')]
        row['title'] = 'PRIVATE_TITLE'
        self.assertFalse(metadata_candidates(self.inventory, self.snapshot).candidates)
        row['bibliographic_identifiers'] = []
        self.assertFalse(metadata_candidates(self.inventory, self.snapshot).candidates)

    def test_conflicting_catalog_records_are_ambiguous_not_confirmed(self):
        self.snapshot.tables['catalog_entries'].append(dict(self.snapshot.tables['catalog_entries'][0]))
        result = metadata_candidates(self.inventory, self.snapshot)
        self.assertEqual(len(result.candidates), 2)
        self.assertEqual(result.ambiguous_files, [0])

    def test_checksum_validation_and_no_inferred_scheme(self):
        self.assertEqual(identifiers({'identifiers':[{'value':'9780306406158','scheme':'ISBN'}]}), ())
        self.assertEqual(identifiers({'identifiers':[{'value':'9780306406157'}]}), ())
        self.assertEqual(identifiers({'identifiers':[{'value':'978-0-306-40615-7','scheme':'ISBN'}]}), (('isbn13','9780306406157'),))
        self.assertEqual(identifiers({'identifiers':[{'value':'0306406152','scheme':'ISBN'}]}), (('isbn10','0306406152'),))
        self.assertEqual(identifiers({'identifiers':[{'value':'same-edition','scheme':'edition'}]}), (('edition','same-edition'),))

    def test_budget_and_cancellation_preserve_incompleteness(self):
        self.snapshot.tables['catalog_entries'] *= 2
        result = metadata_candidates(self.inventory, self.snapshot, max_candidates=1)
        self.assertTrue(result.reasons)
        self.assertLessEqual(len(result.candidates), 1)
        self.assertTrue(metadata_candidates(self.inventory, self.snapshot, cancelled=lambda:True).reasons)

    def test_snapshot_projects_only_bibliographic_identifiers_never_raw_metadata(self):
        path = self.root/'db.sqlite'
        fixture_db(path)
        with sqlite3.connect(path) as db:
            db.execute('INSERT INTO catalog_entries (identity_id,metadata_json) VALUES (?,?)', ('alice', json.dumps({'epub_metadata':{'identifiers':[{'scheme':'ISBN', 'value':'9780306406157'}]},'token':'SECRET'})))
        snapshot = read_quiescent_snapshot(path)
        self.assertFalse(snapshot.reasons)
        row = snapshot.tables['catalog_entries'][0]
        self.assertEqual(row['bibliographic_identifiers'], (('isbn13','9780306406157'),))
        self.assertNotIn('SECRET', repr(row))
        self.assertEqual(len(metadata_candidates(self.inventory, snapshot).candidates), 1)


if __name__ == '__main__':
    unittest.main()
