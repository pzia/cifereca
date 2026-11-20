"""#4988: already-installed local lender discovery is a neutral shared detail.

No provider GET/POST, implicit cross-profile borrow, copy or selection button.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.borrow_for_identity_discovery import PossibleBorrowLender
from cifereca.gui.cross_profile_possible_lender_detail import (
    append_possible_lender_summary as append,
)
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness
from tests.test_core_p1_4662_live_lender_preview import (
    _candidate, harness,
)


def view():
    w = witness()
    candidate = PossibleBorrowLender(
        label="Alice", lender_identity_id=w.account.identity_id,
        _account=w.account, _notice=w.notice,
    )
    read = Mock(return_value=(candidate,))
    service = NS(
        current_identity=NS(identity_id="bob"),
        _account_context_epoch=3, _source_context_epoch=5,
        possible_borrow_lenders_for_current_identity=read,
        execute_cross_profile_borrow=Mock(
            side_effect=AssertionError("no lender POST"),
        ),
    )
    group = Mock()
    pending = []

    def submit(_label, work, done, fail, **kwargs):
        pending.append((work, done, fail))

    panel = NS(
        window=NS(service=service, runner=NS(submit=submit), _route_display_token=object()),
        pending=pending,
        _rendered_search_detail=(w.notice, object()),
        _group=group,
    )
    return w, candidate, service, panel, group, read


def deliver(panel):
    work, done, _ = panel.pending.pop(0)
    done(work())


def test_shared_detail_shows_one_unverified_lender_without_action_or_permission():
    w, candidate, service, panel, group, read = view()
    assert append(panel, w.notice) is True
    read.assert_not_called()
    group.assert_not_called()
    deliver(panel)
    read.assert_called_once_with(exact_notice=w.notice)
    assert group.call_count == 1
    title, rows = group.call_args.args
    assert title == "Emprunt avec un autre profil · information"
    assert len(rows) == 1
    assert rows[0][0] == "Profil prêteur 1"
    assert "Alice" in rows[0][1]
    assert "quota fournisseur non vérifiés" in rows[0][1]
    assert "aucun emprunt lancé" in rows[0][1]
    assert candidate._account.credential_id not in repr(rows)
    assert not candidate.executable
    service.execute_cross_profile_borrow.assert_not_called()


def test_zero_lenders_does_not_create_a_false_no_loan_statement():
    w, candidate, service, panel, group, read = view()
    read.return_value = ()
    assert append(panel, w.notice) is True
    deliver(panel)
    group.assert_not_called()


def test_untrusted_dto_blocks_the_entire_section():
    w, candidate, service, panel, group, read = view()
    object.__setattr__(candidate, "executable", True)
    assert append(panel, w.notice) is True
    deliver(panel)
    group.assert_not_called()


@pytest.mark.parametrize("rebind", ["identity", "account_epoch", "source_epoch", "detail"])
def test_rebind_during_discovery_suppresses_old_detail(rebind):
    w, candidate, service, panel, group, read = view()

    def change(**kwargs):
        if rebind == "identity":
            service.current_identity = NS(identity_id="bob")
        elif rebind == "account_epoch":
            service._account_context_epoch += 1
        elif rebind == "source_epoch":
            service._source_context_epoch += 1
        else:
            panel._rendered_search_detail = (replace(w.notice), object())
        return (candidate,)

    read.side_effect = change
    assert append(panel, w.notice) is True
    deliver(panel)
    group.assert_not_called()


def test_replaced_or_stale_exact_notice_never_reads_lender_state():
    w, candidate, service, panel, group, read = view()
    assert append(panel, replace(w.notice)) is False
    assert not read.called


def test_wrong_type_or_foreign_edition_never_renders():
    w, candidate, service, panel, group, read = view()
    read.return_value = [{"credential": "PRIVATE"}]
    assert append(panel, w.notice) is True
    deliver(panel)
    read.return_value = (replace(candidate, _notice=replace(w.notice)),)
    assert append(panel, w.notice) is True
    deliver(panel)
    group.assert_not_called()


def test_real_core_facade_returns_exact_inert_lender_without_provider_io(harness):
    service, account, session = harness
    notice = _candidate().notice
    rows = service.possible_borrow_lenders_for_current_identity(
        exact_notice=notice,
    )
    assert len(rows) == 1
    candidate, = rows
    assert candidate.lender_identity_id == "alex"
    assert candidate._notice is notice
    assert candidate.executable is False
    assert candidate.supplier_eligibility == "not_verified"
    assert session.calls == 0
    assert service.current_identity.identity_id == "default"


def test_shared_detail_is_integrated_without_post_or_gtk_action():
    source = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/detail_panel.py"
    ).read_text(encoding="utf-8")
    assert "append_possible_lender_summary(self, item)" in source
    reader = (
        Path(__file__).resolve().parents[1]
        / "src/cifereca/gui/cross_profile_possible_lender_detail.py"
    ).read_text(encoding="utf-8")
    assert "panel._group(" in reader
    assert "action_button(" not in reader
    assert ".connect(" not in reader
    assert "borrow_search_item(" not in reader
    assert "execute_cross_profile_borrow(" not in reader


@pytest.mark.parametrize("phase", ["queued", "delivery", "duplicate", "failure"])
def test_async_discovery_is_fenced_and_consumed_once(phase):
    w, candidate, service, panel, group, read = view()
    assert append(panel, w.notice)
    work, done, fail = panel.pending.pop(0)

    def aba():
        panel.window._route_display_token = object()
        panel.window._route_display_token = object()

    if phase == "queued":
        aba()
        assert work() == ()
        read.assert_not_called()
        done(())
        group.assert_not_called()
    else:
        rows = work()
        read.assert_called_once_with(exact_notice=w.notice)
        if phase == "delivery":
            aba()
            done(rows)
            group.assert_not_called()
        elif phase == "failure":
            fail(RuntimeError("read failed"))
            done(rows)
            group.assert_not_called()
        else:
            done(rows)
            done(rows)
            fail(RuntimeError("late failure"))
            group.assert_called_once()
    service.execute_cross_profile_borrow.assert_not_called()


def test_hidden_detail_does_not_receive_lender_summary():
    w, candidate, service, panel, group, read = view()
    panel.get_mapped = lambda: False
    assert append(panel, w.notice)
    deliver(panel)
    group.assert_not_called()
