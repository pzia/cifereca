"""#6397: scope web action delivery to the exact local-view selection."""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.local_public_c_detail_guard import (
    begin_local_public_web_selection, local_public_web_selection_check,
    local_public_c_detail_check,
)
from tests.test_core_p0_6366_local_c_detail_n3_revocation import _view


@pytest.mark.parametrize("view", ["library", "recent"])
def test_local_web_selection_revokes_old_callback_on_same_row_aba(view):
    host, row, item, link, service = _view(view)
    selected = [row]
    visible = [view]
    setattr(host, view + "_page", NS(selection=NS(get_selected_item=lambda: selected[0])))
    host.view_stack = NS(get_visible_child_name=lambda: visible[0])
    proof = local_public_c_detail_check(host, row, item, link, view=view)
    old = begin_local_public_web_selection(host, view)
    check = local_public_web_selection_check(host, row, view=view, token=old, source_current=proof)
    assert check()
    visible[0] = "search"
    assert not check()
    visible[0] = view
    selected[0] = NS(key=row.key)
    assert not check()
    begin_local_public_web_selection(host, view)
    selected[0] = row
    new = begin_local_public_web_selection(host, view)
    assert not check()
    fresh = local_public_web_selection_check(host, row, view=view, token=new, source_current=proof)
    assert fresh()
    service._account_context_epoch += 1
    assert not fresh()
