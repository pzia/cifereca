"""#6366: persisted C epoch crosses the optional public UI port as a receipt."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core import search_orchestration
from cifereca.core.notice_c_cover_index import (
    canonical_cover_document_ref, read_consolidated_notice_cover,
)
from cifereca.gui.plugin_public_c_binding import public_c_binding_revision
from cifereca.gui.plugin_public_c_guard import plugin_public_c_current
from cifereca.plugin_api import UiPublicNoticeCoverBindingHost
from cifereca.storage import Storage
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import (
    _view, _book, _detail, _method,
)
from tests.test_core_p0_6366_same_call_public_cover_writer import witnessed, mirror
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row, OTHER_URL
from tests.test_plugin_ui_host_boundary import host_type


def test_revision_receipt_real_host_list_detail_and_progressive_update(
    tmp_path, monkeypatch,
):
    clock = [200]
    monkeypatch.setattr(search_orchestration.time, "time", lambda: clock[0])
    db = Storage(tmp_path / "plugin-binding.sqlite")
    view, window, service = _view()
    host = host_type()(window)
    assert isinstance(host, UiPublicNoticeCoverBindingHost)
    try:
        first = witnessed(public_row())
        assert mirror(db, [first]) == 1
        doc, packet = host.public_notice_cover_binding(first, identity_id="alice")
        assert doc == host.public_notice_cover_ref(first, identity_id="alice")
        assert public_c_binding_revision(window, doc, packet) == 1
        assert host.public_notice_cover_binding(first, identity_id="alice")[1] == packet
        assert host.public_notice_cover_binding(replace(first), identity_id="alice") is None
        assert host.public_notice_cover_binding(first, identity_id="bob") is None
        # A changed scalar epoch is not a persisted producer witness and
        # cannot be elevated into a newly issued opaque binding.
        first._core_notice_canonical_cover_document_epoch += 1
        assert host.public_notice_cover_binding(first, identity_id="alice") is None
        assert public_c_binding_revision(window, doc, packet) is None
        first._core_notice_canonical_cover_document_epoch -= 1

        def publish(binding):
            ref, opaque = binding
            source = replace(_book(document=ref), cover_document_receipt=opaque)
            view.show_rows((source,))
            visible = view._page.rows[0]
            view._page.selection.current = visible
            cell = NS(window=window, _bind_epoch=3,
                      _bound_key=visible.key, _thumbnail_item=visible)
            assert _method("_admit_core_local_cover")(cell, visible, object(), ref)
            return visible, _method("_search_cover_guard")(cell, visible)

        old, old_options = publish((doc, packet))
        assert old_options["minimum_revision"] == 1
        assert old_options["is_current"]()

        clock[0] = 201
        fresh = witnessed(public_row(thumbnail_url=OTHER_URL))
        assert mirror(db, [fresh]) == 1
        latest, fresh_packet = host.public_notice_cover_binding(fresh, identity_id="alice")
        assert latest == doc and fresh_packet != packet
        assert read_consolidated_notice_cover(db, doc).epoch == 2
        visible, options = publish((doc, fresh_packet))
        assert not old_options["is_current"]()
        assert options["minimum_revision"] == 2
        assert options["is_current"]()

        panel = view._page.detail
        captured = []
        panel.show_presentation = lambda presentation: captured.append((
            presentation.cover_document_ref,
            panel._next_public_c_minimum_revision,
            panel._next_local_public_c_check,
        ))
        view.show_detail(_detail(document=doc))
        assert captured[0][0:2] == (doc, 2)
        check = captured[0][2]
        assert check() and panel._next_public_c_minimum_revision is None
        # Replaying a formerly valid token after its owner/source transition
        # cannot restore the list or detail, even if the C itself still exists.
        service._account_context_epoch += 2
        assert not check() and not options["is_current"]()
        assert public_c_binding_revision(window, doc, fresh_packet) is None
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("kind", ["integer", "unknown", "foreign_c", "foreign_owner", "stale"])
def test_explicit_bad_receipt_is_not_a_legacy_document_bind(tmp_path, kind):
    db = Storage(tmp_path / "bad-receipt.sqlite")
    view, window, service = _view()
    try:
        item = witnessed(public_row())
        assert mirror(db, [item]) == 1
        doc, packet = host_type()(window).public_notice_cover_binding(
            item, identity_id="alice",
        )
        if kind == "integer":
            packet = 123
        elif kind == "unknown":
            packet = object()
        elif kind == "foreign_c":
            doc = canonical_cover_document_ref("edition:other")
        elif kind == "foreign_owner":
            service._source_context_epoch += 1
        else:
            item._core_notice_canonical_cover_document_epoch += 1
        row = replace(_book(document=doc), cover_document_receipt=packet)
        view.show_rows((row,))
        visible = view._page.rows[0]
        assert visible._cover_plugin_c_source is row
        assert visible._cover_plugin_c_receipt is None
        assert not plugin_public_c_current(visible._cover_plugin_c_receipt)
        assert not _method("_admit_core_local_cover")(
            NS(window=window), visible, object(), doc,
        )
        view._page.selection.current = visible
        view.show_detail(_detail(document=doc))
        rendered, guard = view._page.detail.renders[-1]
        assert rendered.cover_document_ref is None
        assert rendered.thumbnail_url is None and guard is None
    finally:
        db.close()
