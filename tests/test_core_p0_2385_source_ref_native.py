"""A mutated C source reference is never a storage/N2/UI authority.

Controlled public + credential-scoped private observations; no provider HTTP,
acquisition, entitlement, placement or alternate bibliography admission.
"""
from contextlib import closing
from dataclasses import replace

import pytest

from cifereca.core.notice_cover_repository import (
    _opaque_key, is_notice_cover_document,
)
from cifereca.storage import Storage
from tests.test_core_p0_6397_c_web_reference_persistence import source
from tests.test_core_p0_6397_authenticated_web_choices import Host


class Hostile:
    def __eq__(self, other):
        raise AssertionError("foreign comparison of Notice C source")

    def __hash__(self):
        raise AssertionError("foreign hash of Notice C source")


@pytest.mark.parametrize("private", (False, True))
@pytest.mark.parametrize("field,damaged", (
    ("source_key", Hostile()), ("kind", Hostile()),
    ("provider_id", Hostile()), ("credential_id", Hostile()),
    ("identity_id", Hostile()), ("account_id", Hostile()),
    ("loan_id", Hostile()), ("artifact_id", Hostile()),
    ("placement_id", Hostile()), ("source_key", "x" * 513),
    ("loan_id", "loan"), ("artifact_id", "artifact"),
    ("placement_id", "placement"), ("source_key", "notice-c:v1:\n"),
))
def test_mutated_c_source_ref_fails_closed_without_foreign_equality(
    tmp_path, private, field, damaged,
):
    with closing(Storage(tmp_path / "source-ref.sqlite")) as db:
        if private:
            host = Host(db)
            account = host.account("alice")
            original, _ = host.source(account)
        else:
            original = source(db, "seller-a", "public-one", None)
        document = original.document
        assert _opaque_key(document) is not None
        assert is_notice_cover_document(document)
        modified = replace(document)
        object.__setattr__(modified, field, damaged)
        assert _opaque_key(modified) is None
        assert not is_notice_cover_document(modified)
        assert _opaque_key(document) is not None
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_private_and_anonymous_source_ref_cannot_be_relabelled_across_scopes(tmp_path):
    with closing(Storage(tmp_path / "source-scope.sqlite")) as db:
        host = Host(db)
        private, _ = host.source(host.account("alice"))
        public = source(db, "seller-a", "public-one", None)
        for doc, edits in (
            (private.document, {"kind": "public_catalogue"}),
            (private.document, {"identity_id": None}),
            (private.document, {"credential_id": "__public__"}),
            (public.document, {"kind": "catalogue"}),
            (public.document, {"identity_id": "alice"}),
            (public.document, {"credential_id": "card-alice"}),
        ):
            forged = replace(doc)
            for name, value in edits.items():
                object.__setattr__(forged, name, value)
            assert _opaque_key(forged) is None
            assert not is_notice_cover_document(forged)
        assert db.entitlements() == db.acquisitions() == db.placements() == []
