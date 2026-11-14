"""P0 #5975 / #5851: direct lender Core workers never read GTK navigation."""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from cifereca.core import borrow_for_identity_live as live
from cifereca.core import borrow_for_identity_action_plan as plan
from cifereca.core import borrow_for_identity_action_scope as scope
from cifereca.core import borrow_for_identity_executor as executor
from tests.test_core_p1_4662_live_lender_preview import (
    _candidate, _preview, harness,
)


def test_lender_read_plan_and_mutation_workers_have_no_navigation_reads():
    for fn in (
        live.preview_live_borrow_for_current_identity,
        live.revalidate_live_borrow_lender_selection,
        plan.prepare_cross_profile_lender_action_plan,
        scope.capture_cross_profile_borrow_scope,
        scope.CrossProfileBorrowScopeLease.current,
        executor.execute_cross_profile_borrow,
    ):
        parsed = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "current_identity"
            for node in ast.walk(parsed)
        ), fn.__qualname__


def test_no_scope_fails_closed_before_lender_sqlite_or_supplier_io(harness):
    service, account, session = harness
    row = _candidate()
    selected = _preview(service, row)
    clicked = selected.options[0].option_id

    with pytest.raises(ValueError, match="invalid_cross_profile_borrow_context"):
        live.preview_live_borrow_for_current_identity(
            service, expected_edition_id="edition-1",
            observations=[row], now=1100,
        )
    assert live.revalidate_live_borrow_lender_selection(
        service, captured_preview=selected, selected_option_id=clicked,
        expected_edition_id="edition-1", observations=(row,), now=1100,
    ).status == "revoked"
    assert plan.prepare_cross_profile_lender_action_plan(
        service, captured_preview=selected, selected_option_id=clicked,
        expected_edition_id="edition-1", observations=(row,), now=1100,
    ).status == "revoked"
    assert scope.capture_cross_profile_borrow_scope(
        service, requesting_identity_id="default", borrowing_account=account,
        exact_notice=row.notice, lender_session=session,
    ) is None
    assert executor.execute_cross_profile_borrow(
        service, captured_preview=selected, selected_option_id=clicked,
        expected_edition_id="edition-1", observations=(row,),
        confirmed=True, now=1100,
    )["status"] == "blocked"
    assert session.calls == 0


def test_direct_headless_proxy_reads_exact_lender_with_explicit_scope(harness):
    service, _account, session = harness
    row = _candidate()

    class Headless:
        def __getattr__(self, name):
            if name == "current_identity":
                raise AssertionError("Core read mutable GTK navigation")
            return getattr(service, name)

    headless = Headless()
    available = lambda: True
    preview = live.preview_live_borrow_for_current_identity(
        headless, expected_edition_id="edition-1",
        requesting_identity_id="default", scope_current=available,
        observations=(row,), now=1100,
    )
    assert preview.status == "external" and len(preview.options) == 1
    selected = preview.options[0].option_id
    current = live.revalidate_live_borrow_lender_selection(
        headless, captured_preview=preview, selected_option_id=selected,
        expected_edition_id="edition-1", observations=(row,), now=1100,
        requesting_identity_id="default", scope_current=available,
    )
    assert current.status == "current"
    prepared = plan.prepare_cross_profile_lender_action_plan(
        headless, captured_preview=preview, selected_option_id=selected,
        expected_edition_id="edition-1", observations=(row,), now=1100,
        requesting_identity_id="default", scope_current=available,
    )
    assert prepared.status == "ready_for_consent"
    assert prepared.executable is prepared.transfer_permitted is False
    assert session.calls == 0


def test_scope_revoke_before_snapshot_and_plan_does_not_read_lender(harness):
    service, _account, session = harness
    row = _candidate()
    preview = _preview(service, row)
    clicked = preview.options[0].option_id
    assert live.revalidate_live_borrow_lender_selection(
        service, captured_preview=preview, selected_option_id=clicked,
        expected_edition_id="edition-1", observations=(row,), now=1100,
        requesting_identity_id="default", scope_current=lambda: False,
    ).status == "revoked"
    assert plan.prepare_cross_profile_lender_action_plan(
        service, captured_preview=preview, selected_option_id=clicked,
        expected_edition_id="edition-1", observations=(row,), now=1100,
        requesting_identity_id="default", scope_current=lambda: False,
    ).status == "revoked"
    assert session.calls == 0
