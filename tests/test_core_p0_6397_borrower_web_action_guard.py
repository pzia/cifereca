"""#6397: exact real borrower C receipt, selection/render and epoch guards."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_borrower_public_projection import borrower_public_c_receipt
from cifereca.gui.borrower_public_c_bridge import (
    borrower_public_web_selection_check, show_public_loan_c, show_public_history_c,
)
from cifereca.storage import Storage
from tests.test_core_p0_6366_borrower_public_c_lists_detail import (
    admit, bibliography, cell, host_for, history, loan,
)


@pytest.mark.parametrize("is_loan", [True, False])
def test_public_web_only_in_exact_borrower_render(tmp_path, monkeypatch, is_loan):
    from cifereca.gui import notice_c_web_action_controller as actions
    db = Storage(tmp_path / "borrower-web.sqlite")
    try:
        _, c = bibliography(db)
        source = loan() if is_loan else history()
        assert admit(db, [source]) == 1
        receipt = borrower_public_c_receipt(source, identity_id="alice")
        host = host_for(source)
        host._loan_cover_decision_revision = 1
        row = cell(host, source, receipt)
        route = "loans" if is_loan else "history"
        page = host.loan_page if is_loan else host.history_page
        panel = page.detail
        selected = [row]
        visible = [route]
        page.selection = NS(get_selected_item=lambda: selected[0])
        host.view_stack = NS(get_visible_child_name=lambda: visible[0])
        def render(item, *, document):
            assert item is source and document == c.document
            panel._action_buttons = []
        panel.show_loan_public_c = render
        panel.show_history_public_c = render
        calls = []
        def request(owner, target, document, *, expected_epoch, current):
            assert owner is host and target is panel
            assert document == c.document and expected_epoch == c.epoch
            assert current()
            calls.append(current)
            return True
        monkeypatch.setattr(actions, "request_public_web_actions", request)
        def show():
            if is_loan:
                return show_public_loan_c(host, source, lambda: source)
            return show_public_history_c(host, source, source)
        assert show()
        old, = calls
        assert old()
        visible[0] = "search"
        assert not old()
        visible[0] = route
        selected[0] = NS(key=row.key)
        assert not old()
        selected[0] = row
        assert show()  # Same-row full redraw revokes old delayed menu.
        fresh = calls[-1]
        assert not old() and fresh()
        host.service._source_context_epoch += 1
        assert not fresh()
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()
