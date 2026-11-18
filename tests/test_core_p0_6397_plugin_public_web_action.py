"""#6397: an opaque public C receipt is mandatory for plugin detail web actions."""
from dataclasses import replace

from cifereca.core import search_orchestration
from cifereca.gui.plugin_public_c_binding import issue_public_c_binding
from cifereca.gui.plugin_public_c_guard import (
    request_plugin_public_web_action, selected_plugin_public_c,
)
from tests.test_core_p0_6366_cached_public_notice_readthrough import _Public, _service
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view, _book, _detail


def test_plugin_web_action_requires_worker_receipt_and_exact_render(tmp_path, monkeypatch):
    from cifereca.gui import notice_c_web_action_controller as actions
    service = _service(tmp_path)
    session = _Public()
    service._sessions = {session.provider_id: session}
    try:
        source = service.search("Progressive Book", provider_id=session.provider_id, force=True)[0]
        ref = source._core_notice_canonical_cover_document_ref
        cached = service.search("Progressive Book", provider_id=session.provider_id, force=False)[0]
        search_orchestration._stamp_cached_public_notice_refs(
            [cached], identity_id="alice", storage=service.storage,
        )
        view, window, owner = _view()
        page = view._page
        panel = page.detail
        panel._action_buttons = []
        mapped = [True]
        page.get_mapped = lambda: mapped[0]
        calls = []
        def request(host, detail, document, *, expected_epoch, current):
            assert host is window and detail is panel
            assert current()
            calls.append((document, expected_epoch, current))
            return True
        monkeypatch.setattr(actions, "request_public_web_actions", request)

        # Legacy plugin ref may paint a cover but cannot grant a web action.
        view.show_rows((_book(document=ref),))
        page.selection.current = page.rows[0]
        view.show_detail(_detail(document=ref))
        assert calls == []

        document, binding = issue_public_c_binding(window, cached, identity_id="alice")
        assert document == ref
        exact = replace(_book(document=ref), cover_document_receipt=binding)
        view.show_rows((exact,))
        page.selection.current = page.rows[0]
        view.show_detail(_detail(document=ref))
        (actual, epoch, old), = calls
        assert actual == ref and epoch == 1 and old()
        mapped[0] = False
        assert not old()
        mapped[0] = True
        panel._action_buttons = []  # full detail render, same selected row
        assert not old()
        pinned = selected_plugin_public_c(view, ref)
        assert not request_plugin_public_web_action(view, panel, pinned, expected_epoch=2)
        assert not request_plugin_public_web_action(
            view, panel, pinned, expected_epoch=1, selection_is_current=lambda: False,
        )
        assert request_plugin_public_web_action(view, panel, pinned, expected_epoch=1)
        fresh = calls[-1][2]
        assert fresh()
        owner._account_context_epoch += 1
        assert not fresh()
        assert session.calls == 1
    finally:
        service.close()
