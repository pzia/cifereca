"""#6366: real C cursor after restart, without HTTP or an actionable source."""
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
from cifereca.core.notice_c_cover_index import read_consolidated_notice_cover
from cifereca.core.notice_cover_repository import _opaque_key
from cifereca.gui.plugin_public_c_binding import public_c_binding_revision
from cifereca.storage import Storage
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view, _book, _detail, _method
from tests.test_core_p0_6366_same_call_public_cover_writer import witnessed, mirror
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_plugin_ui_host_boundary import host_type


def test_offline_broker_reissues_exact_c_to_neutral_list_and_detail(tmp_path):
    filename = tmp_path / "known-recommendation.sqlite"
    db = Storage(filename)
    try:
        live = witnessed(public_row(title="La forêt connue"))
        assert mirror(db, [live]) == 1
        ref = live._core_notice_canonical_cover_document_ref
        epoch = read_consolidated_notice_cover(db, ref).epoch
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM metadata WHERE key=?",
                        (_opaque_key(live._core_notice_cover_document_ref),))
    finally:
        db.close()

    restored = Storage(filename)
    try:
        view, window, service = _view()
        catalog = CatalogBrokerImpl(NS(storage=restored))
        items = catalog.known_public_notices(
            "forêt", scope="digital", provider_ids=("bnf",),
        )
        assert len(items) == 1
        item = items[0]
        assert catalog.known_public_notices(
            "forêt", provider_ids=("unrelated",),
        ) == []
        assert item.source_credential == "" and item.source_account_id is None
        assert item.downloadable is None and item.owned is None
        assert item.thumbnail_url is item.friendly_url is item.download_url is None
        assert item.metadata["notice_c_known"] is True
        assert known_public_c_binding_facts(item) == (ref, epoch)
        assert known_public_c_binding_facts(replace(item)) is None
        host = host_type()(window)
        assert host.public_notice_cover_ref(item, identity_id="alice") == ref
        document, opaque = host.public_notice_cover_binding(item, identity_id="alice")
        assert document == ref
        assert public_c_binding_revision(window, ref, opaque) == epoch
        view.show_rows((replace(_book(document=ref), cover_document_receipt=opaque),))
        row = view._page.rows[0]
        view._page.selection.current = row
        cell = NS(window=window, _bound_key=row.key, _thumbnail_item=row, _bind_epoch=1)
        assert _method("_admit_core_local_cover")(cell, row, object(), ref)
        options = _method("_search_cover_guard")(cell, row)
        assert options["minimum_revision"] == epoch and options["is_current"]()
        panel = view._page.detail
        captured = []
        panel.show_presentation = lambda p: captured.append((
            p.cover_document_ref, panel._next_public_c_minimum_revision,
            panel._next_local_public_c_check,
        ))
        view.show_detail(_detail(document=ref))
        assert captured[0][:2] == (ref, epoch)
        assert captured[0][2]()
        item.source_credential = "__public__"
        assert not options["is_current"]() and not captured[0][2]()
        assert host.public_notice_cover_binding(item, identity_id="alice") is None
        assert restored.entitlements() == [] and restored.acquisitions() == []
    finally:
        restored.close()
