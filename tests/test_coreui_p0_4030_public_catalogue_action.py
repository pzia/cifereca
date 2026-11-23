"""#4030: explicit public catalogue action and one-shot scope in CoreUI."""
from types import SimpleNamespace as NS
import pytest

from cifereca.gui import local_origin_suggestion_action as action
from cifereca.gui.local_public_c_detail_guard import begin_local_public_web_selection
from tests.test_core_p0_4030_public_catalogue import rig
from tests.test_core_p0_3171_atomic_publication_v14 import store
from tests.test_coreui_p0_4030_suggestion_action import Button


def _ui(rig):
    service, session, request, calls, path = rig
    row = NS(key="book")
    item = {"identity_id": "A", "placement_id": request.placement_id, "local": True}
    tasks, buttons, groups, toasts = [], [], [], []
    panel = NS(add_actions=buttons.extend,
               add_group=lambda title, lines: groups.append((title, lines)))

    def button(label, _icon):
        value = Button()
        value.label = label
        return value

    host = NS(service=service, _library_objects={"book": item},
              _route_display_token=object(), _closed=False,
              library_page=NS(selection=NS(get_selected_item=lambda: row)),
              view_stack=NS(get_visible_child_name=lambda: "library"),
              runner=NS(submit=lambda label, work, done, failed, **kw:
                        tasks.append((work, done, failed))),
              action_button=button, toast=toasts.append)
    token = begin_local_public_web_selection(host, "library")
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    assert len(buttons) == 1 and not tasks and not calls
    buttons[0].callback(buttons[0])
    work, done, _ = tasks[0]
    done(work())
    assert len(groups) == 1 and len(buttons) == 2 and not calls
    assert buttons[1].label == "Envoyer le titre à new-catalogue"
    return host, buttons[1], tasks, groups, toasts


def test_public_lookup_requires_second_click_and_is_not_replayed(rig):
    host, button, tasks, groups, _ = _ui(rig)
    _, _, _, calls, _ = rig
    button.callback(button)
    button.callback(button)
    assert len(tasks) == 2 and not calls
    work, done, _ = tasks[1]
    result = work()
    assert len(calls) == 1
    done(result)
    done(result)
    assert len(groups) == 2
    assert "ISBN déclaré, titre et auteur concordants" not in repr(groups[1])
    assert repr(groups[1]).count("ISBN déclaré ambigu") == 2
    assert "aucune origine ni droit attestés" in repr(groups[1])
    button.callback(button)
    assert len(tasks) == 2 and len(calls) == 1


@pytest.mark.parametrize("change", ["route", "selection", "epoch", "session"])
def test_revoked_public_choice_never_calls_supplier(rig, change):
    host, button, tasks, groups, _ = _ui(rig)
    service, session, _, calls, _ = rig
    button.callback(button)
    if change == "route":
        host._route_display_token = object()
    elif change == "selection":
        begin_local_public_web_selection(host, "library")
    elif change == "epoch":
        service._source_context_epoch += 1
    else:
        service._sessions["public"] = NS(**vars(session))
    work, done, _ = tasks[1]
    done(work())
    assert not calls and len(groups) == 1


def test_late_public_response_cannot_populate_new_detail(rig):
    host, button, tasks, groups, _ = _ui(rig)
    _, _, _, calls, _ = rig
    button.callback(button)
    work, done, _ = tasks[1]
    result = work()
    assert len(calls) == 1
    begin_local_public_web_selection(host, "library")
    done(result)
    assert len(groups) == 1


def test_public_legacy_detail_retains_same_isbn_distinct_formats(rig):
    from dataclasses import replace
    from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
    from tests.test_core_p0_4030_public_catalogue import _row
    from tests.test_core_p0_3171_atomic_publication_v14 import rows

    host, button, tasks, groups, _ = _ui(rig)
    service, session, _, calls, path = rig
    a = _row("same")
    b = replace(_row("same"), id="public:same:pdf", format="PDF")
    response = ProviderSearchResponse(
        [a, b], public_notices=(PublicSearchNotice(a), PublicSearchNotice(b)),
    )
    def search(query, scope, **kwargs):
        calls.append((query, scope, kwargs))
        return response
    session.search_with_response_facets = search
    before, content = rows(service.storage), path.read_bytes()
    button.callback(button)
    work, done, _ = tasks[-1]
    value = work()
    assert value["status"] == "partial"
    done(value)
    done(value)
    text = repr(groups[-1])
    assert text.count("ISBN déclaré ambigu") == 2
    assert text.count("Référence catalogue : same") == 2
    assert text.count("Collection catalogue : CATALOGUE") == 2
    assert text.count("Format catalogue : EPUB") == 1
    assert text.count("Format catalogue : PDF") == 1
    assert "ISBN déclarés par le catalogue : 9780306406157" in text
    assert len(calls) == 1 and rows(service.storage) == before
    assert path.read_bytes() == content
