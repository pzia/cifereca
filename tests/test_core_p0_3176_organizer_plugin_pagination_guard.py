"""P0 #3176: a prepared or selected page is never silently abandoned."""
from dataclasses import replace
from importlib.util import spec_from_file_location, module_from_spec
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api.library_organizer import OrganizerPageAdvice


def _controller():
    plugin = Path(__file__).resolve().parents[1] / (
        "src/plugins/maintenance-library-organizer/cifereca_library_organizer.py"
    )
    spec = spec_from_file_location("organizer_review_page_guard", plugin)
    mod = module_from_spec(spec)
    spec.loader.exec_module(mod)
    tasks, calls, rendered = [], [], []
    host = NS(identity_id="A", is_current=lambda: True,
              submit=lambda _label, work, done: tasks.append((work, done)) or True)
    broker = NS(
        next_page=lambda **kwargs: calls.append(("next", kwargs)) or None,
        confirm_dry_run=lambda **kwargs: calls.append(("confirm", kwargs)) or None,
        cancel=lambda **kwargs: calls.append(("cancel", kwargs)) or None,
    )
    controller = mod.GuidedOrganizer(broker, host, lambda: True,
                                     lambda page, busy: rendered.append((page, busy)))
    return controller, broker, tasks, calls, rendered


def _page(**changes):
    page = OrganizerPageAdvice(
        token="exact-private-page", page=1, placement_count=3,
        group_count=1, groups=(), eligible=1, selected=0,
        has_more=True, inventory_status="partial",
    )
    return replace(page, **changes)


@pytest.mark.parametrize("state,selected", [
    ("prepared", 0), ("confirmed", 0), ("", 1), ("empty", 0),
])
def test_next_page_does_not_discard_prepared_or_selected_review(state, selected):
    controller, broker, tasks, calls, rendered = _controller()
    prior = controller.page = _page(dry_run_state=state, selected=selected)
    assert controller.request("next") is False
    assert controller.page is prior and not controller.busy
    assert tasks == [] and calls == [] and rendered == []
    # Explicit cancellation is still available and revokes this exact page.
    assert controller.cancel() is True
    assert calls == [("cancel", {"token": prior.token})]
    assert controller.page is None


def test_prepared_confirmation_is_not_displaced_by_attempted_pagination():
    controller, broker, tasks, calls, rendered = _controller()
    page = controller.page = _page(dry_run_state="prepared")
    assert not controller.request("next")
    assert controller.request("confirm") is True
    assert controller.page is None and len(tasks) == 1
    work, done = tasks.pop()
    work()
    assert calls == [("confirm", {"token": page.token, "confirmed": True})]
    done(None)
    assert not controller.busy
    assert not controller.request("next")


def test_ordinary_unselected_page_continues_through_one_worker_only():
    controller, broker, tasks, calls, rendered = _controller()
    page = controller.page = _page()
    assert controller.request("next") is True
    assert not controller.request("next")
    assert not calls and len(tasks) == 1
    work, done = tasks.pop()
    work()
    assert calls == [("next", {"token": page.token, "confirmed": True})]
    done(None)
    assert not controller.busy
