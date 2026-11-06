"""P0 #3196: Kobo exposes a revision only from the authenticated manifest.

Real provider adapter, fake private Vault response, no acquisition/GET of
content, no legacy backup writes and no migration authorization.
"""
from __future__ import annotations

import pytest

from cifereca.plugin_api import from_legacy_rights_snapshot
from tests.test_p1_3252_kobo_selected_delivery_offer import setup


@pytest.mark.parametrize("revision", ["rev-current", "a" * 64])
def test_scoped_kobo_purchase_carries_exact_manifest_revision(revision):
    session, vault, _broker, account, posts = setup()
    vault.rows[0]["revision_id"] = revision
    try:
        snapshot = session.rights_observation_snapshot(account)
        assert snapshot.scope.account_id == account.unique_id
        row = snapshot.exact("book-1", "book-1", "purchase")
        assert row is not None and row.provider_attested
        assert row.provider_revision_id == revision
        v2 = from_legacy_rights_snapshot(snapshot)
        assert v2.resources[0].provider_revision_id == revision
        assert v2.resources[0].authorizes_acquisition is False
        assert snapshot.authorizes_mutation is False
        assert len(posts) == 0
    finally:
        session.close()


@pytest.mark.parametrize("raw", [42, True, "", " padded", "tail ", "bad\nrev", "x" * 257])
def test_kobo_catalog_normalization_never_coerces_a_revision_witness(raw):
    session, vault, _broker, _account, posts = setup()
    vault.rows[0]["revision_id"] = raw
    try:
        normalized = session.normalize_item(vault.rows[0])
        assert normalized.revision_id is None
        assert posts == []
    finally:
        session.close()


def test_missing_personal_revision_does_not_inherit_product_id():
    session, vault, _broker, account, posts = setup()
    vault.rows[0].pop("revision_id", None)
    try:
        rights = session.rights_observation_snapshot(account)
        row = rights.exact("book-1", "book-1", "purchase")
        assert row is not None
        assert row.provider_revision_id is None
        assert posts == []
    finally:
        session.close()
