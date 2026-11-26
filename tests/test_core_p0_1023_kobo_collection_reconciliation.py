"""#1023: deterministic, fictional Kobo collection reconciliation.

Exercise the real scoped SQLite mirror, retained public C and both local
matching paths. These are NOT anonymized captures or actual Kobo totals.
"""
from contextlib import closing
from types import SimpleNamespace as NS

from cifereca.core import search_orchestration
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.core.search import filter_loaded_results, local_match_explanation
from cifereca.storage import Storage
from tests.test_core_p0_6366_c_first_known_search import _known
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


# Record IDs and text are invented; the two homonyms represent distinct
# editions. The absent and outside rows make the scope difference explicit.
COLLECTION = (
    ("exact", "Prix du Quai", None, {}),
    ("long-title", "Prix du Quai des Orfèvres", None, {}),
    ("no-stopword", "Prix Quai", None, {}),
    ("award", "Un roman", None, {"awards": ["Prix du Quai"]}),
    ("subject", "Un roman", None, {"subjects": ["Prix du Quai"]}),
    ("collection", "Un roman", None, {"collection": "Prix du Quai"}),
    ("summary", "Un roman", "Prix du Quai", {}),
    ("split", "Le prix", "Une enquête sur le quai", {}),
    ("absent", "Le prix littéraire", None, {}),
)
EXPECTED = {item[0] for item in COLLECTION} - {"absent"}


def ids(rows):
    return {row["item_id"] for row in rows}


def test_kobo_collection_known_scope_and_relevance_after_restart(tmp_path):
    filename = tmp_path / "collection.sqlite"
    with closing(Storage(filename)) as db:
        private = [
            public_row(
                source_provider="kobo", record_id=key, id=key,
                source_credential="fixture-account", source_account_id="fixture-owner",
                source_collection="library", revision_id="edition-" + key,
                title=title, description=description, metadata=metadata,
                owned=True, downloadable=False,
            )
            for key, title, description, metadata in COLLECTION
        ]
        assert search_orchestration._mirror_remote_results(
            NS(storage=db), private, identity_id="alice",
        ) == len(COLLECTION)
        outside = persist(db, verified_public(
            source_provider="kobo", record_id="outside", title="Prix du Quai",
        ))
        public = persist_exact_public_search_cover(db, outside)
        assert evict_notice_cover(db, outside.document, expected_epoch=outside.epoch)

    with closing(Storage(filename)) as db:
        stored = db.catalog_entries(identity_id="alice", provider_id="kobo")
        assert len(stored) == len(COLLECTION)
        assert ids(stored) == EXPECTED | {"absent"}
        # Library's matcher consumes the same persisted descriptive values,
        # not a fabricated entitlement or a second set of field rules.
        library = [
            {**row, "metadata": dict(row["metadata"]), "local": False}
            for row in stored
        ]
        for query in ("prix du quai", "PRIX quai", "prix quai"):
            selected = filter_loaded_results(library, query)
            assert ids(selected) == EXPECTED and len(selected) == len(EXPECTED)
            known = _known(db, query, provider_ids=("kobo",))
            assert ids(known) == EXPECTED | {"outside"}
            assert len(known) == len(EXPECTED) + 1
            # The foreign public record is visible but the collection is not.
            assert ids(_known(db, query, identity="bob", provider_ids=("kobo",))) == {"outside"}
            assert _known(db, query, provider_ids=("foreign",)) == []
            positions = {row["item_id"]: i for i, row in enumerate(selected)}
            assert positions["exact"] < positions["summary"]
            assert positions["long-title"] < positions["summary"]
        for field, expected in (
            ("title", {"exact", "long-title"}),
            ("subject", {"subject"}),
            ("collection", {"collection"}),
        ):
            assert ids(filter_loaded_results(library, "prix du quai", field=field)) == expected
            expected_known = expected | ({"outside"} if field == "title" else set())
            assert ids(_known(db, "prix du quai", field=field,
                              provider_ids=("kobo",))) == expected_known
        for key, reason in (
            ("award", "Prix littéraire"), ("subject", "Sujet"),
            ("collection", "Collection"), ("summary", "Résumé"),
        ):
            row = next(row for row in library if row["item_id"] == key)
            assert reason in local_match_explanation(row, "prix du quai")
        page = search_public_notice_c(db, "prix du quai", group_by_document=True)
        assert page.status == "partial" and not page.has_more
        assert page.known_matches == page.known_documents == 1
        hit, = page.hits
        assert hit.document == public.document and hit.record_id == "outside"
        assert db.entitlements() == [] and db.acquisitions() == []
