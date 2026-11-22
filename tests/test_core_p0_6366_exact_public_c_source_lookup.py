"""#6366: exact public-source C lookup, no cross-provider title/ISBN join."""
from __future__ import annotations

from cifereca.core.notice_c_exact_source_lookup import exact_public_source_c
from cifereca.core.notice_c_cover_index import (
    persist_consolidated_cover_source, persist_exact_public_search_cover,
)
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import (
    persist, verified_public,
)

URL_A = "https://covers.example.org/a.jpg"
URL_B = "https://covers.example.org/b.jpg"


def make(db, provider, record, *, docbase="digital-books", revision=None):
    return persist(db, verified_public(
        source_provider=provider, record_id=record, docbase=docbase,
        revision_id=revision, title="Titre identique",
        thumbnail_url=URL_A if provider == "bnf" else URL_B,
    ))


def find(db, source):
    bibliography = source.observation.fields_copy()["search_result"]["public_bibliography"]
    return exact_public_source_c(
        db, provider_id=source.observation.key.provider_id,
        record_id=bibliography["record_id"], docbase=bibliography["docbase"],
        medium=bibliography["medium"],
        source_revision=bibliography["revision_id"],
    )


def test_source_lookup_prefers_one_attested_multisource_c_after_source_purge(tmp_path):
    db = Storage(tmp_path / "source-lookup.sqlite")
    try:
        a = make(db, "bnf", "edition-a")
        b = make(db, "numilog-catalog", "edition-b")
        own_a = persist_exact_public_search_cover(db, a)
        own_b = persist_exact_public_search_cover(db, b)
        assert find(db, a).document == own_a.document
        assert find(db, b).document == own_b.document
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-relationship",
            source=a, relation_proof_id="strong:fixture:a",
        )
        shared = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:verified-relationship",
            source=b, relation_proof_id="strong:fixture:b",
        )
        assert find(db, a).document == find(db, b).document == shared.document
        assert {cover.url for cover in find(db, a).candidates} == {URL_A, URL_B}
        with db._lock, db._connect() as sql:
            for source in (a, b):
                sql.execute(
                    "DELETE FROM metadata WHERE key=?",
                    (_opaque_key(source.document),),
                )
        assert find(db, a).document == shared.document
        assert find(db, b).document == shared.document
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_homonyms_and_incomplete_coordinates_never_join(tmp_path):
    db = Storage(tmp_path / "no-inference.sqlite")
    try:
        a = make(db, "bnf", "same-record", docbase="books-a", revision="rev-a")
        b = make(db, "bnf", "same-record", docbase="books-b", revision="rev-b")
        c = make(db, "numilog-catalog", "same-record", docbase="books-a")
        ca = persist_exact_public_search_cover(db, a)
        cb = persist_exact_public_search_cover(db, b)
        cc = persist_exact_public_search_cover(db, c)
        assert len({ca.document, cb.document, cc.document}) == 3
        assert find(db, a).document == ca.document
        assert find(db, b).document == cb.document
        assert find(db, c).document == cc.document
        for args in (
            dict(provider_id="bnf", record_id="same-record",
                 docbase="books-a", medium="digital", source_revision=None),
            dict(provider_id="bnf", record_id="same-record",
                 docbase="", medium="digital", source_revision="rev-a"),
            dict(provider_id="bnf", record_id="same-record",
                 docbase="books-a", medium="loan", source_revision="rev-a"),
            dict(provider_id="numilog-catalog", record_id="same-record",
                 docbase="books-a", medium="digital", source_revision="rev-a"),
            dict(provider_id="bnf", record_id="same-record",
                 docbase="books-a", medium="digital",
                 source_revision="https://signed.example/token"),
        ):
            assert exact_public_source_c(db, **args) is None
        assert db.entitlements() == []
    finally:
        db.close()


def test_two_conflicting_equal_strength_groups_fail_closed(tmp_path):
    db = Storage(tmp_path / "conflict.sqlite")
    try:
        a = make(db, "bnf", "edition-a")
        b = make(db, "numilog-catalog", "edition-b")
        c = make(db, "paris", "edition-c")
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:group-one",
            source=a, relation_proof_id="strong:fixture:a",
        )
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:group-one",
            source=b, relation_proof_id="strong:fixture:b",
        )
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:group-two",
            source=a, relation_proof_id="strong:fixture:a",
        )
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:group-two",
            source=c, relation_proof_id="strong:fixture:c",
        )
        assert find(db, a) is None
        assert find(db, b) is not None
        assert find(db, c) is not None
    finally:
        db.close()


def test_unequal_multisource_relations_are_still_ambiguous(tmp_path):
    """More sources are not proof that a conflicting canonical claim is false."""
    db = Storage(tmp_path / "unequal-conflicts.sqlite")
    try:
        a = make(db, "bnf", "edition-a")
        b = make(db, "numilog-catalog", "edition-b")
        c = make(db, "paris", "edition-c")
        d = make(db, "another-provider", "edition-d")
        for source in (a, b, c):
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:larger-group",
                source=source, relation_proof_id="strong:fixture:larger",
            )
        for source in (a, d):
            persist_consolidated_cover_source(
                db, canonical_edition_id="edition:smaller-group",
                source=source, relation_proof_id="strong:fixture:smaller",
            )
        assert find(db, a) is None
        assert find(db, b).document == find(db, c).document
        assert find(db, d) is not None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


def test_single_source_c_does_not_override_unique_multisource_relation(tmp_path):
    db = Storage(tmp_path / "one-relation.sqlite")
    try:
        a = make(db, "bnf", "edition-a")
        b = make(db, "numilog-catalog", "edition-b")
        persist_exact_public_search_cover(db, a)
        persist_consolidated_cover_source(
            db, canonical_edition_id="edition:attested-group",
            source=a, relation_proof_id="strong:fixture:a",
        )
        group = persist_consolidated_cover_source(
            db, canonical_edition_id="edition:attested-group",
            source=b, relation_proof_id="strong:fixture:b",
        )
        assert find(db, a).document == group.document
        assert find(db, b).document == group.document
    finally:
        db.close()


def test_batch_uses_one_sqlite_read_and_independent_conflict_veto(tmp_path, monkeypatch):
    from cifereca.core.notice_c_exact_source_lookup import exact_public_source_c_many

    db = Storage(tmp_path / "batch-source-lookup.sqlite")
    try:
        a = make(db, "bnf", "edition-a")
        b = make(db, "numilog-catalog", "edition-b")
        c = make(db, "paris", "edition-c")
        d = make(db, "other-provider", "edition-d")
        own = persist_exact_public_search_cover(db, d)
        for member in (a, b):
            left = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:group-left",
                source=member, relation_proof_id="strong:fixture:left",
            )
        for member in (a, c):
            right = persist_consolidated_cover_source(
                db, canonical_edition_id="edition:group-right",
                source=member, relation_proof_id="strong:fixture:right",
            )
        keys = [
            ("bnf", "edition-a", "digital-books", "digital", None),
            ("numilog-catalog", "edition-b", "digital-books", "digital", None),
            ("paris", "edition-c", "digital-books", "digital", None),
            ("other-provider", "edition-d", "digital-books", "digital", None),
        ]
        connects = []
        connect = db._connect

        def counted():
            connects.append(1)
            return connect()

        monkeypatch.setattr(db, "_connect", counted)
        resolved = exact_public_source_c_many(
            db, [*keys, keys[1], ("bnf", "", "digital-books", "digital", None)],
        )
        assert len(connects) == 1
        assert {key: snapshot.document for key, snapshot in resolved.items()} == {
            keys[1]: left.document, keys[2]: right.document, keys[3]: own.document,
        }
        assert exact_public_source_c_many(db, [keys[0]] * 257) == {}
        assert exact_public_source_c_many(db, []) == {}
        assert len(connects) == 1
    finally:
        db.close()


def test_exact_lookup_survives_large_catalogue_and_per_source_overflow(tmp_path, monkeypatch):
    import json
    from cifereca.core import notice_c_exact_source_lookup as lookup
    from cifereca.core.notice_c_cover_index import (
        _db_key, canonical_cover_document_ref,
    )

    db = Storage(tmp_path / "large-catalogue.sqlite")
    try:
        target = make(db, "bnf", "wanted-edition")
        noisy = make(db, "paris", "unrelated-edition")
        wanted = persist_exact_public_search_cover(db, target)
        unrelated = persist_exact_public_search_cover(db, noisy)
        with db._lock, db._connect() as sql:
            raw = sql.execute(
                "SELECT value FROM metadata WHERE key=?",
                (_db_key(unrelated.document),),
            ).fetchone()[0]
            payload = json.loads(raw)
            rows = []
            # Independent canonical rows sharing another exact source:
            # valid persisted JSON, deliberately ambiguous for THAT source.
            for n in range(4100):
                edition = f"edition:unrelated-claim-{n}"
                payload["edition_id"] = edition
                rows.append((_db_key(canonical_cover_document_ref(edition)),
                             json.dumps(payload)))
            sql.executemany("INSERT INTO metadata(key,value) VALUES(?,?)", rows)
            sql.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?)",
                (_db_key(canonical_cover_document_ref("edition:corrupt")), "{bad json"),
            )
            sql.execute("DELETE FROM metadata WHERE key LIKE 'notice.c.observation.v1:%'")

        decode = lookup._decode
        seen = []
        def counted(raw, document):
            seen.append(document)
            return decode(raw, document)
        monkeypatch.setattr(lookup, "_decode", counted)
        key = ("bnf", "wanted-edition", "digital-books", "digital", None)
        noisy_key = ("paris", "unrelated-edition", "digital-books", "digital", None)
        result = lookup.exact_public_source_c_many(db, [key])
        assert result[key].document == wanted.document
        assert seen == [wanted.document]
        seen.clear()
        result = lookup.exact_public_source_c_many(db, [key, noisy_key])
        assert len(seen) <= lookup._MAX_RESULTS + 2
        assert set(result) == {key}
        assert result[key].document == wanted.document
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
