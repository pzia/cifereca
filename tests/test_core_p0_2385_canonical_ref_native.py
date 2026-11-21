"""Canonical C refs are validated before public web/read-side comparisons.

SQLite with controlled public source; no provider HTTP, rights or acquisition.
"""
from contextlib import closing
from dataclasses import replace

import pytest

from cifereca.core.notice_c_cover_index import (
    _db_key, is_canonical_cover_document,
)
from cifereca.core.notice_c_web_choices import (
    read_public_web_choices, resolve_public_web_choice,
)
from cifereca.storage import Storage
from tests.test_core_p0_6397_c_web_reference_persistence import (
    source, link, publish,
)


class Hostile:
    def __eq__(self, other):
        raise AssertionError("foreign C DocumentRef equality")

    def __hash__(self):
        raise AssertionError("foreign C DocumentRef hash")

    def startswith(self, other):
        raise AssertionError("foreign C source key prefix")


@pytest.mark.parametrize("field,value", [
    ("kind", Hostile()), ("source_key", Hostile()),
    ("provider_id", Hostile()), ("credential_id", Hostile()),
    ("loan_id", Hostile()), ("artifact_id", Hostile()),
    ("placement_id", Hostile()), ("identity_id", Hostile()),
    ("account_id", Hostile()),
    ("loan_id", "nonempty"), ("artifact_id", "nonempty"),
    ("placement_id", "nonempty"), ("source_key", "x" * 513),
    ("kind", "public_catalogue\x0a"),
])
def test_tampered_canonical_document_ref_is_not_a_c_click_or_storage_key(
    tmp_path, field, value,
):
    with closing(Storage(tmp_path / "canonical-ref.sqlite")) as db:
        origin = source(
            db, "seller-a", "edition-1", [
                link("seller-a", "https://books.example.org/title/one"),
            ],
        )
        canonical = publish(db, origin.observation.key.edition_id, origin)
        doc = canonical.document
        menu = read_public_web_choices(db, doc)
        option, = menu.choices
        assert is_canonical_cover_document(doc)
        assert resolve_public_web_choice(
            db, doc, expected_epoch=menu.epoch, choice_id=option.choice_id,
        ) == "https://books.example.org/title/one"

        damaged = replace(doc)
        object.__setattr__(damaged, field, value)
        assert not is_canonical_cover_document(damaged)
        with pytest.raises(ValueError, match="invalid_canonical_cover_ref"):
            _db_key(damaged)
        assert read_public_web_choices(db, damaged) is None
        assert resolve_public_web_choice(
            db, damaged, expected_epoch=menu.epoch, choice_id=option.choice_id,
        ) is None
        assert is_canonical_cover_document(doc)
        assert db.entitlements() == db.acquisitions() == db.placements() == []
