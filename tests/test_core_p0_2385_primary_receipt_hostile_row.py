"""A mutable view row cannot make the sealed primary C receipt call foreign equality.

Controlled public source, canonical retention and restart. No supplier HTTP,
private rights, origin, artifact or placement.
"""
from contextlib import closing
from dataclasses import replace

from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_known_projection import (
    known_public_primary_facts, known_public_alternative_facts,
    merge_public_notice_c_known_rows,
)
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.detail_model import book_detail_presentation
from cifereca.gui.search_known_public_c_bibliography import with_known_public_field_provenance
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def test_hostile_row_fields_cannot_invoke_equality_or_mint_primary_c_provenance(tmp_path):
    filename = tmp_path / "hostile-primary.sqlite"
    with closing(Storage(filename)) as db:
        source = persist(db, verified_public(
            record_id="primary-row", title="Notice primaire",
            publisher="Éditeur source",
        ), at=100)
        persist_exact_public_search_cover(db, source)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as db:
        row, = merge_public_notice_c_known_rows(
            [], search_public_notice_c(db, "Notice primaire", now=100),
            identity_id="alice",
        )
        assert known_public_primary_facts(row) is not None

        class Hostile:
            def __eq__(self, other):
                raise AssertionError("external equality executed")

            def __ne__(self, other):
                raise AssertionError("external inequality executed")

        presentation = book_detail_presentation(
            title="Notice primaire", section_rows=((
                "Catalogue", (("Éditeur", "Éditeur source"),),
            ),),
        )
        for key in ("source", "identity_id", "_cover_document_ref",
                    "provider_id", "item_id", "title", "notice_c_epoch"):
            candidate = {**row, "metadata": dict(row["metadata"])}
            if key == "notice_c_epoch":
                candidate["metadata"][key] = Hostile()
            else:
                candidate[key] = Hostile()
            assert known_public_primary_facts(candidate) is None
            assert known_public_alternative_facts(candidate) == ()
            assert with_known_public_field_provenance(presentation, candidate) is presentation
        packet = row["_core_notice_c_primary_facts"]
        for member in ("kind", "source_key", "provider_id",
                       "credential_id", "identity_id", "account_id"):
            corrupted = replace(packet[2].document)
            object.__setattr__(corrupted, member, Hostile())
            for position in ("view", "source"):
                candidate = dict(row)
                if position == "view":
                    candidate["_cover_document_ref"] = corrupted
                else:
                    candidate["_core_notice_c_primary_facts"] = (
                        packet[0], packet[1],
                        replace(packet[2], document=corrupted),
                    )
                assert known_public_primary_facts(candidate) is None
                assert with_known_public_field_provenance(presentation, candidate) is presentation
        for forged_packet in (
            (packet[0], Hostile(), packet[2]),
            (packet[0], packet[1], replace(packet[2], epoch=Hostile())),
        ):
            candidate = {**row, "_core_notice_c_primary_facts": forged_packet}
            assert known_public_primary_facts(candidate) is None
            assert known_public_alternative_facts(candidate) == ()
            assert with_known_public_field_provenance(presentation, candidate) is presentation
        assert db.entitlements() == db.acquisitions() == db.placements() == []



def test_mutable_public_c_page_rejected_before_foreign_len_hash_or_equality(tmp_path):
    import pytest

    class Hostile:
        def __len__(self):
            raise AssertionError("foreign C page length")

        def __hash__(self):
            raise AssertionError("foreign C page hash")

        def __eq__(self, other):
            raise AssertionError("foreign C page equality")

    with closing(Storage(tmp_path / "hostile-page.sqlite")) as db:
        source = persist(db, verified_public(
            record_id="primary-page", title="Page primaire",
        ), at=100)
        persist_exact_public_search_cover(db, source)
        page = search_public_notice_c(
            db, "Page primaire", group_by_document=True, now=100,
        )
        good, = merge_public_notice_c_known_rows(
            [], page, identity_id="alice",
        )
        assert good["item_id"] == "primary-page"

        altered_document = replace(page.hits[0].document)
        object.__setattr__(altered_document, "source_key", Hostile())
        altered_source = replace(page.hits[0], document=altered_document)
        failures = (
            replace(page, hits=Hostile()),
            replace(page, hits=(Hostile(),)),
            replace(page, matching_sources=Hostile()),
            replace(page, matching_sources=(altered_source,)),
            replace(page, hits=(altered_source,)),
            replace(page, known_documents=Hostile()),
            replace(page, known_matches=Hostile()),
            replace(page, scanned_sources=Hostile()),
            replace(page, unindexed_sources=Hostile()),
            replace(page, status=Hostile()),
            replace(page, has_more=Hostile()),
        )
        for candidate in failures:
            with pytest.raises(ValueError, match="invalid_known_public_c_projection"):
                merge_public_notice_c_known_rows([], candidate, identity_id="alice")
        good_again, = merge_public_notice_c_known_rows(
            [], page, identity_id="alice",
        )
        assert good_again["item_id"] == good["item_id"]
        assert db.entitlements() == db.acquisitions() == db.placements() == []
