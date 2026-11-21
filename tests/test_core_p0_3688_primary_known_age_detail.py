"""Primary public C age is shown only with its own immutable source receipt.

Controlled same-call producer and SQLite; UI selection is the existing
headless Search detail harness. No live provider, authority or network.
"""
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.search_known_public_c_age import primary_known_public_c_ages
from cifereca.storage import Storage
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_c_first_gtk_list_detail import _detail_selected
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_search_results_view import _host


def test_exact_primary_age_reaches_selected_search_detail_after_restart(tmp_path):
    filename = tmp_path / "primary-age.sqlite"
    with closing(Storage(filename)) as db:
        source = persist(db, project(original()), at=100)
        canonical = persist_exact_public_search_cover(db, source)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as db:
        item, = merge_public_notice_c_known_rows(
            [], search_public_notice_c(db, "Même titre", now=100),
            identity_id="alice",
        )
        assert item["_cover_document_ref"] == canonical.document
        proof, = primary_known_public_c_ages(item)
        assert (proof.age_min, proof.age_max, proof.age_kind) == (12, None, "publisher")
        assert proof.source_label == "À partir de 12 ans"
        assert proof.provenance.provider_id == item["provider_id"]
        assert proof.provenance.source_id == item["item_id"]
        assert proof.provenance.source == item["provider_id"] + ".fixture.age"

        ui = _host()
        ui._identity_context_generation = 2
        ui._search_generation = 3
        ui.service._account_context_epoch = 5
        ui.service._source_context_epoch = 7
        ui._populate_local_search([item])
        row, = ui.search_page.show_rows.call_args.args[0]
        panel = NS(show_presentation=Mock(), empty=Mock())
        host = NS(
            _closed=False, _search_detail_request_generation=0,
            _cancel_cached_book_details=Mock(),
            _search_local_objects={item["key"]: item},
            _selected_search_id=None, service=ui.service, _search_mode="local",
            _identity_context_generation=2, _search_generation=3,
            search_page=NS(detail=panel,
                           selection=NS(get_selected_item=lambda: row)),
        )
        _detail_selected()(host, row)
        panel.empty.assert_not_called()
        presentation = panel.show_presentation.call_args.args[0]
        age_fields = [field for section in presentation.sections
                      for field in section.fields if field.label == "Âge éditeur"]
        assert len(age_fields) == 1
        assert age_fields[0].value == "À partir de 12 ans"
        assert age_fields[0].provenance == proof.provenance
        assert age_fields[0].kind == "copy"
        assert presentation.cover_document_ref == canonical.document
        assert not presentation.actions and not presentation.links
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_unattested_or_changed_primary_age_never_mints_trusted_detail_metadata(tmp_path):
    with closing(Storage(tmp_path / "age-negative.sqlite")) as db:
        source = persist(db, project(original()), at=100)
        persist_exact_public_search_cover(db, source)
        item, = merge_public_notice_c_known_rows(
            [], search_public_notice_c(db, "Même titre", now=100),
            identity_id="alice",
        )
        assert len(primary_known_public_c_ages(item)) == 1
        for change in ("age", "wire", "kind", "profile", "record", "packet"):
            altered = {**item, "metadata": deepcopy(item["metadata"])}
            if change == "age":
                altered["metadata"]["youth_age_ranges"][0]["age_min"] = 6
            elif change == "wire":
                altered["metadata"]["bibliography_evidence"]["youth_age"]["source_field"] = "changed"
            elif change == "kind":
                altered["metadata"]["youth_age_ranges"][0]["age_kind"] = "reading"
            elif change == "profile":
                altered["identity_id"] = "bob"
            elif change == "record":
                altered["item_id"] = "unrelated"
            else:
                altered.pop("_core_notice_c_primary_facts")
            assert primary_known_public_c_ages(altered) == ()
        assert db.entitlements() == db.acquisitions() == db.placements() == []



def test_copied_search_primary_receipt_never_hashes_foreign_age_bounds(tmp_path):
    from dataclasses import replace

    class Hostile:
        def __hash__(self):
            raise AssertionError("foreign age bound hash")

        def __eq__(self, other):
            raise AssertionError("foreign age bound equality")

    with closing(Storage(tmp_path / "hostile-primary-age.sqlite")) as db:
        source = persist(db, project(original()), at=100)
        persist_exact_public_search_cover(db, source)
        item, = merge_public_notice_c_known_rows(
            [], search_public_notice_c(db, "Même titre", now=100),
            identity_id="alice",
        )
        assert len(primary_known_public_c_ages(item)) == 1
        token, identity, hit = item["_core_notice_c_primary_facts"]
        age = hit.age_observation
        assert age is not None
        malformed = (
            replace(age, ranges=(replace(age.ranges[0], age_min=Hostile()),)),
            replace(age, ranges=Hostile()),
            replace(age, source_field=Hostile()),
            replace(age, record_id="another-edition"),
            replace(age, ranges=(replace(age.ranges[0], age_min=121),)),
            replace(age, ranges=(replace(age.ranges[0], age_kind="inferred"),)),
        )
        for bad in malformed:
            modified = {
                **item,
                "_core_notice_c_primary_facts": (
                    token, identity, replace(hit, age_observation=bad),
                ),
            }
            assert primary_known_public_c_ages(modified) == ()
        assert len(primary_known_public_c_ages(item)) == 1
        assert db.entitlements() == db.acquisitions() == db.placements() == []
