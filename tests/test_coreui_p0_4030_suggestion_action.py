"""#4030: explicit Library action and stale-result veto, synthetic GTK host."""
from types import SimpleNamespace as NS
import pytest

from cifereca.gui import local_origin_suggestion_action as action
from cifereca.gui.local_public_c_detail_guard import begin_local_public_web_selection
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_core_p0_4030_suggestion_operator import _service


class Button:
    def set_tooltip_text(self, _value):
        pass

    def connect(self, _signal, callback):
        self.callback = callback

    def get_mapped(self):
        return True


def _host(store, tmp_path):
    service, root, pid = _service(store, tmp_path)
    row = NS(key="book")
    item = {"identity_id": "A", "placement_id": pid, "local": True}
    tasks, buttons, groups, toasts = [], [], [], []
    panel = NS(add_actions=buttons.extend,
               add_group=lambda title, lines: groups.append((title, lines)))
    host = NS(service=service, _library_objects={"book": item},
              _route_display_token=object(), _closed=False,
              library_page=NS(selection=NS(get_selected_item=lambda: row)),
              view_stack=NS(get_visible_child_name=lambda: "library"),
              runner=NS(submit=lambda label, work, done, failed, **kw:
                        tasks.append((work, done, failed))),
              action_button=lambda *a: Button(), toast=toasts.append)
    token = begin_local_public_web_selection(host, "library")
    return host, panel, row, item, token, tasks, buttons, groups, toasts


def test_no_implicit_search_and_only_descriptive_results(store, tmp_path):
    host, panel, row, item, token, tasks, buttons, groups, _ = _host(store, tmp_path)
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    assert not tasks and not groups
    button, = buttons
    button.callback(button)
    work, done, _ = tasks[0]
    assert not groups
    done(work())
    assert len(groups) == 1
    assert "Roman commun" in repr(groups)
    assert "aucune origine ni droit attestés" in repr(groups)
    assert item["placement_id"] not in repr(groups)
    button.callback(button)
    assert len(tasks) == 1


@pytest.mark.parametrize("change", ["selection", "route", "epoch", "row", "revoked"])
def test_stale_callback_never_displays_results(store, tmp_path, change):
    host, panel, row, item, token, tasks, buttons, groups, _ = _host(store, tmp_path)
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    button, = buttons
    button.callback(button)
    work, done, _ = tasks[0]
    result = work()
    if change == "selection":
        begin_local_public_web_selection(host, "library")
    elif change == "route":
        host._route_display_token = object()
    elif change == "epoch":
        host.service._source_context_epoch += 1
    elif change == "row":
        host._library_objects["book"] = dict(item)
    else:
        button._cifereca_action_context_revoked = True
    done(result)
    assert not groups


def test_foreign_item_never_offers_action(store, tmp_path):
    host, panel, row, item, token, tasks, buttons, groups, _ = _host(store, tmp_path)
    item["identity_id"] = "B"
    assert not action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    assert not buttons and not tasks and not groups
