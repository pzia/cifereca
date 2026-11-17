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


def test_future_lender_declared_primary_selection_is_checked_without_brand(
    harness,
):
    """A non-Paris lender with a typed selected-source key gets the same veto.

    The witness must not call the provider or use the current GTK identity.
    """
    from dataclasses import replace
    from cifereca.providers.base import ManualBorrowPolicy
    service, account, session = harness
    provider = "future-lender"
    account.provider_id = provider
    session.provider_id = provider
    session.descriptor = replace(
        session.descriptor, id=provider,
        manual_borrow_policy=ManualBorrowPolicy(
            docbase="DILICOM", primary_borrower_id="",
            source_selection_key="chosen_future_connection",
            verification="provider_observed",
        ),
    )
    service._sessions[service._session_key(provider, account.credential_id)] = session
    service._provider_plugin_ids[provider] = "fixture.paris"
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": provider,
        "credential_id": account.credential_id,
        "label": "Fictive future lender", "enabled": True, "metadata": {},
    })
    service.config.upsert_provider_account({
        "id": account.credential_id, "provider": provider,
        "identity_id": "alex", "enabled": True,
    })
    configured = service.config.identity("alex")
    configured["chosen_future_connection"] = account.credential_id
    service.config.upsert_identity(configured)

    candidate = _candidate()
    candidate = replace(
        candidate,
        notice=replace(candidate.notice, source_provider=provider),
        evidence=replace(candidate.evidence, provider_id=provider),
        edition_evidence=replace(
            candidate.edition_evidence, provider_id=provider,
        ),
    )
    accepted = _preview(service, candidate)
    assert len(accepted.options) == 1
    assert accepted.status == "external"
    assert accepted.options[0].executable is False
    assert service.current_identity.identity_id == "default"
    assert session.calls == 0

    configured = service.config.identity("alex")
    configured["chosen_future_connection"] = "foreign-credential"
    service.config.upsert_identity(configured)
    refused = _preview(service, candidate)
    assert refused.options == ()
    assert refused.rejection_reasons == ("lender_unavailable",)
    assert session.calls == 0


def test_invalid_typed_lender_policy_is_not_an_override(harness):
    from dataclasses import replace
    service, account, session = harness
    session.descriptor = replace(
        session.descriptor, manual_borrow_policy="malformed",
    )
    result = _preview(service, _candidate())
    assert result.options == ()
    assert result.rejection_reasons == ("capability_unverified",)
    assert session.calls == 0


def test_explicit_reader_previews_and_revalidates_without_navigation(harness):
    from cifereca.core.search_orchestration import capture_search_read_scope
    service, _account, session = harness

    class Headless:
        def __getattr__(self, name):
            if name in {"current_identity", "_account_context_epoch"}:
                raise AssertionError("navigation read")
            return getattr(service, name)

    core = Headless()
    scope = capture_search_read_scope(core, "default")
    row = _candidate()
    captured = live.preview_borrow_for_scope(
        core, reader_scope=scope, expected_edition_id="edition-1",
        observations=(row,), now=1100,
    )
    assert captured.preview.status == "external"
    assert not captured.preview.executable
    service._account_context_epoch += 2
    again = live.preview_borrow_for_scope(
        core, reader_scope=scope, expected_edition_id="edition-1",
        observations=(row,), now=1100,
    )
    assert captured.preview == again.preview
    result = live.revalidate_borrow_for_scope(
        core, captured=captured, selected_option_id=captured.preview.options[0].option_id,
        expected_edition_id="edition-1", observations=(row,), now=1100,
    )
    assert result.status == "current" and not result.executable
    assert session.calls == 0


@pytest.mark.parametrize("change", ["source", "reader", "core", "session"])
def test_explicit_lender_read_scope_revoked_without_provider_io(harness, change):
    from dataclasses import replace
    from cifereca.core.search_orchestration import capture_search_read_scope
    service, _account, session = harness
    scope = capture_search_read_scope(service, "default")
    row = _candidate()
    captured = live.preview_borrow_for_scope(
        service, reader_scope=scope, expected_edition_id="edition-1",
        observations=(row,), now=1100,
    )
    core = service
    if change == "source":
        service._source_context_epoch += 1
    elif change == "reader":
        old = service.identity_by_id("default")
        service.identities = [replace(i) if i is old else i for i in service.identities]
    elif change == "session":
        session._epoch += 1
    else:
        core = object()
    result = live.revalidate_borrow_for_scope(
        core, captured=captured, selected_option_id=captured.preview.options[0].option_id,
        expected_edition_id="edition-1", observations=(row,), now=1100,
    )
    assert result.status == "revoked" and not result.executable
    assert session.calls == 0


def test_headless_lender_service_facades_bind_original_reader(harness):
    service, _account, session = harness
    row = _candidate()
    service.select_identity("alex")
    captured = service.preview_borrow_for_identity_scope(
        identity_id="default", expected_edition_id="edition-1",
        observations=(row,), now=1100,
    )
    assert captured.preview.requesting_identity_id == "default"
    assert captured.preview.status == "external"
    kwargs = dict(captured=captured, selected_option_id=captured.preview.options[0].option_id,
                  expected_edition_id="edition-1", observations=(row,), now=1100)
    assert service.revalidate_scoped_borrow_lender_choice(**kwargs).status == "current"
    service._source_context_epoch += 1
    assert service.revalidate_scoped_borrow_lender_choice(**kwargs).status == "revoked"
    assert service.current_identity.identity_id == "alex"
    assert session.calls == 0
    with pytest.raises(ValueError, match="search_identity_unknown"):
        service.preview_borrow_for_identity_scope(
            identity_id="missing", expected_edition_id="edition-1",
            observations=(row,), now=1100,
        )
