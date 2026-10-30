"""Ambiguous persisted source consent is invalid, never an implicit ON."""
import hashlib
import json
from types import SimpleNamespace as NS
import unittest

from cifereca.core.source_sync_consent import _decode


class SourceConsentDecodeTests(unittest.TestCase):
    def setUp(self):
        self.scope = NS(identity_id='profile', provider_id='fixture-provider',
                        credential_id='connection', account_id='account')
        self.row = dict(schema='cifereca.source-sync-consent/1',
                        scope=['profile', 'fixture-provider', 'connection', 'account'],
                        enabled=True, revision='a' * 32)

    def test_valid_on_off_and_unset_keep_existing_meaning(self):
        self.assertEqual(_decode(json.dumps(self.row), self.scope).mode, 'on')
        self.row['enabled'] = False
        self.assertEqual(_decode(json.dumps(self.row), self.scope).mode, 'off')
        self.assertEqual(_decode(None, self.scope).mode, 'unset')

    def test_conflicting_fields_never_grant_the_last_value(self):
        for field, value in dict(enabled=False, schema='old-schema',
                                 scope=['foreign'] * 4, revision='b' * 32).items():
            with self.subTest(field=field):
                raw = '{' + json.dumps(field) + ':' + json.dumps(value) + ',' + json.dumps(self.row)[1:]
                self.assertTrue(json.loads(raw)['enabled'])
                parsed = _decode(raw, self.scope)
                self.assertEqual(parsed.mode, 'invalid')
                self.assertTrue(parsed.configured)
                self.assertEqual(parsed.revision, 'invalid:' + hashlib.sha256(raw.encode()).hexdigest())

    def test_repeated_identical_fields_are_ambiguous_too(self):
        for field, value in self.row.items():
            with self.subTest(field=field):
                raw = '{' + json.dumps(field) + ':' + json.dumps(value) + ',' + json.dumps(self.row)[1:]
                self.assertEqual(_decode(raw, self.scope).mode, 'invalid')

    def test_escaped_duplicate_field_cannot_bypass_uniqueness(self):
        raw = '{"\\u0065nabled":false,' + json.dumps(self.row)[1:]
        self.assertEqual(_decode(raw, self.scope).mode, 'invalid')
