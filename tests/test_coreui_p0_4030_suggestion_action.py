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
    assert "Dernière observation C (UTC)" in repr(groups)
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


def test_next_page_requires_new_click_and_delivers_only_once(store, tmp_path):
    from tests.test_core_p0_4030_suggestion_operator import _more_public
    host, panel, row, item, token, tasks, buttons, groups, _ = _host(store, tmp_path)
    _more_public(store, 21)
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    button, = buttons
    button.callback(button)
    first_work, first_done, _ = tasks[0]
    result = first_work()
    assert result["suggestions"].has_more
    first_done(result)
    first_done(result)
    assert len(groups) == len(tasks) == 1
    assert ("Page", "1") in groups[0][1]
    assert "page suivante" in repr(groups[0])
    button.callback(button)
    button.callback(button)
    assert len(tasks) == 2
    work, done, _ = tasks[1]
    done(work())
    assert len(groups) == 2 and ("Page", "2") in groups[1][1]
    button.callback(button)
    assert len(tasks) == 2


def test_next_page_not_queued_after_origin_context_revocation(store, tmp_path):
    from tests.test_core_p0_4030_suggestion_operator import _more_public
    host, panel, row, item, token, tasks, buttons, groups, toasts = _host(store, tmp_path)
    _more_public(store, 21)
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    button, = buttons
    button.callback(button)
    work, done, _ = tasks[0]
    done(work())
    host.service._source_context_epoch += 1
    button.callback(button)
    assert len(tasks) == len(groups) == 1
    assert toasts and "Contexte modifié" in toasts[-1]


def test_observation_date_is_utc_and_does_not_claim_network_freshness():
    assert action._known_c_observation_label(100) == (
        "Dernière observation C (UTC) : 1970-01-01 00:01"
    )
    assert action._known_c_observation_label(0) == (
        "Dernière observation C (UTC) : 1970-01-01 00:00"
    )
    assert "réseau" not in action._known_c_observation_label(100)


@pytest.mark.parametrize("untrusted", [None, True, 2.5, -1, "123", 10 ** 100])
def test_unusable_c_timestamp_never_exposes_raw_value(untrusted):
    assert action._known_c_observation_label(untrusted) == (
        "Dernière observation C : date indisponible"
    )


def test_known_c_native_detail_disambiguates_editions_and_revisions(store, tmp_path):
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public

    host, panel, row, item, token, tasks, buttons, groups, _ = _host(store, tmp_path)
    persist(store, verified_public(
        record_id="second-edition", revision_id="rev-other",
        title="Roman commun", author="Autrice",
    ), at=100)
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    assert not tasks and not groups
    button, = buttons
    button.callback(button)
    result = tasks[0][0]()
    assert result["status"] == "partial"
    hits = [entry.hit for entry in result["suggestions"].suggestions]
    assert len(hits) == 2 and len({hit.edition_id for hit in hits}) == 2
    tasks[0][1](result)
    assert len(groups) == 1
    displayed = [value for title, value in groups[0][1] if title == "Roman commun"]
    assert len(displayed) == 2
    for hit in hits:
        matches = [value for value in displayed
                   if hit.record_id in value and ("Édition C : " + hit.edition_id) in value]
        assert len(matches) == 1
        assert "Support C : " + hit.medium in matches[0]
        if hit.source_revision:
            assert "Révision C : " + hit.source_revision in matches[0]
    assert "aucune origine ni droit attestés" in repr(groups)
    assert not store.entitlements()

@pytest.mark.parametrize("known,expected", [
    (0, "Aucune correspondance connue ; absence non prouvée."),
    (2, "Correspondances C connues, aucune fiche présentable sur cette page ; absence non prouvée."),
])
def test_empty_local_c_window_does_not_hide_known_source_matches(
    store, tmp_path, known, expected,
):
    from dataclasses import replace

    host, panel, row, item, token, tasks, buttons, groups, _ = _host(store, tmp_path)
    assert action.add_local_origin_suggestion_action(host, panel, row, item, token=token)
    button, = buttons
    button.callback(button)
    original = tasks[0][0]()
    assert original["status"] == "partial"
    page = replace(original["suggestions"], suggestions=(), known_matches=known)
    tasks[0][1]({**original, "suggestions": page})
    assert len(groups) == 1
    assert ("Résultats connus", str(known)) in groups[0][1]
    assert ("Résultat", expected) in groups[0][1]
    assert not store.entitlements()
