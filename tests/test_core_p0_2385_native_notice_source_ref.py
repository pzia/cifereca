"""Native C source scope is revalidated at opaque-key/read boundary.

Public source SQLite and private source key are controlled fixtures. Neither
test creates A (entitlement) nor B (artifact/placement), and no GET is used.
"""
from contextlib import closing
from dataclasses import replace

import pytest

from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import (
    _opaque_key, is_notice_cover_document, read_notice_cover,
)
from cifereca.core.notice_source_observation import NoticeSourceKey
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


class Hostile:
    def __hash__(self):
        raise AssertionError("foreign source hash")

    def __eq__(self, other):
        raise AssertionError("foreign source equality")

    def __ne__(self, other):
        raise AssertionError("foreign source inequality")

    def isprintable(self):
        raise AssertionError("foreign source method")

    def startswith(self, other):
        raise AssertionError("foreign source prefix")


@pytest.mark.parametrize("name,value", [
    ("kind", Hostile()), ("source_key", Hostile()),
    ("provider_id", Hostile()), ("credential_id", Hostile()),
    ("identity_id", Hostile()), ("account_id", Hostile()),
    ("loan_id", Hostile()), ("artifact_id", Hostile()),
    ("placement_id", Hostile()),
    ("kind", "catalogue"), ("provider_id", ""),
    ("credential_id", "private"), ("identity_id", "alice"),
    ("account_id", "alice"), ("loan_id", "loan"),
    ("artifact_id", "sha256:fake"), ("placement_id", "placed"),
    ("source_key", "notice-c:v1:" + "a" * 513),
])
def test_mutated_public_source_ref_never_reaches_sqlite(
    tmp_path, name, value,
):
    with closing(Storage(tmp_path / "public-native.sqlite")) as db:
        source = persist(db, verified_public(
            record_id="native-public", title="C source publique",
        ))
        doc = source.document
        assert is_notice_cover_document(doc)
        assert _opaque_key(doc) is not None
        assert read_notice_cover(db, doc) is not None

        forged = replace(doc)
        object.__setattr__(forged, name, value)
        assert _opaque_key(forged) is None
        assert not is_notice_cover_document(forged)
        assert read_notice_cover(db, forged) is None
        assert read_notice_cover(db, doc) == source
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("name,value", [
    ("source_key", Hostile()), ("identity_id", Hostile()),
    ("account_id", Hostile()), ("credential_id", Hostile()),
    ("provider_id", Hostile()), ("kind", Hostile()),
    ("identity_id", None), ("credential_id", ""),
    ("provider_id", ""), ("kind", "public_catalogue"),
    ("loan_id", "loan"), ("placement_id", "local"),
])
def test_private_catalogue_source_ref_cannot_cross_scope_after_mutation(
    name, value,
):
    doc = notice_cover_document_ref(NoticeSourceKey(
        provider_id="bnf-pret", visibility="private",
        identity_id="alice", credential_id="bnf-credential",
        account_id=None, branch_id="search.authenticated.catalogue",
        record_id="source-one", edition_id="edition-one",
        manifestation_role="digital",
    ))
    assert is_notice_cover_document(doc)
    assert _opaque_key(doc) is not None
    forged = replace(doc)
    object.__setattr__(forged, name, value)
    assert _opaque_key(forged) is None
    assert not is_notice_cover_document(forged)
    assert is_notice_cover_document(doc)


def test_non_document_is_not_a_c_source():
    assert not is_notice_cover_document(Hostile())
    with pytest.raises(TypeError, match="exact Notice C DocumentRef"):
        _opaque_key(Hostile())
