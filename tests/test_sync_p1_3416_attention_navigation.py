"""#3416: sticky attention banner reaches exact rows without provider I/O.

Provider-agnostic synthetic Core DTOs and actual AST-executed GTK controller.
The test harness is headless; real GTK E2E remains a separate CI gate.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.sync_attention_navigation import (
    next_sync_attention_target, focus_sync_attention_target,
)
from tests.test_core_ui_1347_sync_action_scope import _fixture, _source


def test_targets_are_errors_first_then_warnings_with_wrap_and_no_info():
    a, b, c, d = object(), object(), object(), object()
    targets = [("warning", a), ("info", b), ("error", c), ("warning", d)]
    selected = []
    position = 0
    for _ in range(5):
        item, position = next_sync_attention_target(targets, position)
        selected.append(item)
    assert selected == [c, a, d, c, a]
    assert next_sync_attention_target([("info", b)], 0) == (None, 0)
    assert next_sync_attention_target([("error", c)], True) == (c, 0)
    assert next_sync_attention_target([("error", c)] * 1025, 0) == (None, 0)
    assert next_sync_attention_target("secret", 0) == (None, 0)


class Bounds:
    def __init__(self, y):
        self.y = y

    def get_y(self):
        return self.y


class Adjustment:
    def __init__(self, *, upper=950.0, page_size=250.0):
        self.lower = 0.0
        self.upper = upper
        self.page_size = page_size
        self.value = 0.0

    def get_lower(self): return self.lower
    def get_upper(self): return self.upper
    def get_page_size(self): return self.page_size
    def set_value(self, value): self.value = value


def scroller(adjustment):
    return NS(get_child=lambda: object(), get_vadjustment=lambda: adjustment)


def target(y, *, mapped=True, focus=None):
    focus = [] if focus is None else focus
    return NS(
        get_mapped=lambda: mapped,
        compute_bounds=lambda parent: (True, Bounds(y)),
        grab_focus=lambda: (focus.append(y) or True),
    )


@pytest.mark.parametrize(("y", "expected"), [
    (12, 0), (175, 159), (1500, 700),
])
def test_scroll_targets_are_clamped_inside_own_viewport(y, expected):
    adjustment = Adjustment()
    focus = []
    assert focus_sync_attention_target(scroller(adjustment), target(y, focus=focus))
    assert adjustment.value == expected
    assert focus == [y]


def test_stale_or_invalid_row_never_scrolls_or_steals_focus():
    a = Adjustment()
    scroll = scroller(a)
    assert not focus_sync_attention_target(scroll, target(75, mapped=False))
    assert not focus_sync_attention_target(scroll, NS())
    assert not focus_sync_attention_target(
        scroll, NS(compute_bounds=lambda _parent: (False, None),
                   grab_focus=lambda: True),
    )
    assert a.value == 0
    bad = target(float("nan"))
    assert not focus_sync_attention_target(scroll, bad)
    assert a.value == 0


def test_sticky_banner_cycles_actual_actionable_gtk_rows_not_provider_names():
    sources = [
        _source(source_id="warning-source", provider_id="fictional-1",
                status="partial", error="Synchronisation partielle"),
        _source(source_id="error-source", provider_id="fictional-2",
                status="failed", error="PRIVATE_SECRET_URL"),
        _source(source_id="healthy-source", provider_id="fictional-3",
                status="success"),
    ]
    window, _broker, calls, _build, _page = _fixture(sources=sources)
    jump = window._sync_attention_jump_button
    assert jump.get_visible() is True
    assert len(window._sync_attention_targets) == 2
    assert "2 avertissement" not in window._sync_attention_text.label
    assert "PRIVATE_SECRET_URL" not in window._sync_attention_text.label

    adjustment = Adjustment()
    scroll = window._sync_scroll
    scroll.get_vadjustment = lambda: adjustment
    focus = []
    for severity, row in window._sync_attention_targets:
        y = 360 if severity == "error" else 660
        row.compute_bounds = lambda parent, y=y: (True, Bounds(y))
        row.grab_focus = lambda y=y: (focus.append(y) or True)

    jump.click()
    assert adjustment.value == 344
    assert focus == [360]
    jump.click()
    assert adjustment.value == 644
    assert focus == [360, 660]
    jump.click()
    assert focus == [360, 660, 360]
    assert calls == [("sources", "alice")]
    assert window.runner.tasks == []


def test_stale_identity_or_replaced_page_revokes_jump_without_any_io():
    window, _broker, calls, build, _page = _fixture(sources=[
        _source(source_id="fail", provider_id="future", status="failed"),
    ])
    old = window._sync_attention_jump_button
    navigated = []
    row = window._sync_attention_targets[0][1]
    row.compute_bounds = lambda parent: (True, Bounds(100))
    row.grab_focus = lambda: (navigated.append("focus") or True)
    window._sync_scroll.get_vadjustment = lambda: Adjustment()

    window.service.current_identity = NS(identity_id="bob")
    old.click()
    assert not navigated
    window.service.current_identity = NS(identity_id="alice")
    build()  # Replaces page token even when the same identity is selected.
    old.click()
    assert not navigated
    assert calls == [("sources", "alice")]
    assert not window.runner.tasks


def test_information_only_and_clean_page_offer_no_false_navigation():
    window, _broker, calls, _build, _page = _fixture(sources=[
        _source(source_id="idle", provider_id="new-vendor", status="idle"),
    ])
    assert window._sync_attention_jump_button.get_visible() is False
    assert window._sync_attention_targets == []
    window._sync_attention_jump_button.click()
    assert calls == [("sources", "alice")]
    assert window.runner.tasks == []
