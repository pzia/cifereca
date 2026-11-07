"""#4858 D2: manual Kobo preview/confirmation must bind the issuing session.

The upstream account ID alone is not an authenticated Scope. These tests
exercise the real broker and SQLite source configuration without network or
file acquisition, preserving the Kobo-Vault "default" identity adapter label.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests.test_source_materialization_confirmation import case  # noqa: F401
from tests.test_core_p0_4858_manual_canonical_materialization import canonical  # noqa: F401


@pytest.mark.parametrize(("mutation", "value"), (
    ("provider_id", "paris"),
    ("credential_id", "vault-other"),
    ("identity_id", "reader-3"),
    ("unique_id", "foreign-borrower"),
))
@pytest.mark.parametrize("mode", ("legacy", "canonical"))
def test_preview_rejects_wrong_session_account_coordinates_before_provider(
    request, mode, mutation, value,
):
    rig = request.getfixturevalue("canonical" if mode == "canonical" else "case")
    case = rig.case if mode == "canonical" else rig
    account = case.target.accounts[0]
    # The selected Core Account and SQL owner remain unchanged; ONLY the
    # issuing provider session's borrowed coordinates are corrupted.
    case.target._accounts = [replace(account, **{mutation: value})]
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] == "ambiguous_account"
    assert "confirmation_token" not in result.facts
    assert not case.downloaded
    assert not any(case.calls.values())
    if mode == "canonical":
        assert rig.calls == []


@pytest.mark.parametrize("mode", ("legacy", "canonical"))
def test_preview_rejects_stale_registry_alias_before_provider(request, mode):
    rig = request.getfixturevalue("canonical" if mode == "canonical" else "case")
    case = rig.case if mode == "canonical" else rig
    credential = case.target.credential_id
    selected_key = case.service._session_key("kobo", credential)
    assert case.service._sessions.pop(selected_key) is case.target
    case.service._sessions["stale-alias"] = case.target
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok
    assert result.facts["status"] == "source_changed"
    assert "confirmation_token" not in result.facts
    assert not case.downloaded and not any(case.calls.values())
    if mode == "canonical":
        assert rig.calls == []


@pytest.mark.parametrize(("mode", "mutation", "value"), (
    ("legacy", "identity_id", "reader-3"),
    ("canonical", "identity_id", "reader-3"),
    ("legacy", "credential_id", "other-vault"),
    ("canonical", "credential_id", "other-vault"),
))
def test_confirmation_revalidates_exact_borrower_after_preview(
    request, mode, mutation, value,
):
    rig = request.getfixturevalue("canonical" if mode == "canonical" else "case")
    case = rig.case if mode == "canonical" else rig
    preview = case.broker.prepare_local_materialization(case.source)
    assert preview.ok
    original = case.target.accounts[0]
    case.target._accounts = [replace(original, **{mutation: value})]
    token = preview.facts["confirmation_token"]
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok
    assert result.facts["status"] == "ambiguous_account"
    assert not case.downloaded
    if mode == "canonical":
        assert rig.calls == []


@pytest.mark.parametrize("mode", ("legacy", "canonical"))
def test_kobo_default_adapter_label_remains_valid_for_named_core_owner(
    request, mode,
):
    rig = request.getfixturevalue("canonical" if mode == "canonical" else "case")
    case = rig.case if mode == "canonical" else rig
    account = case.target.accounts[0]
    assert account.identity_id != "default"
    case.target._accounts = [replace(account, identity_id="default")]
    from cifereca.core import source_materialization as materialize
    # The source admission MUST recognize Kobo-Vault's adapter-only label.
    # The old 0.7 manifest may independently require reconciliation, which
    # does not mean the account binding was rejected.
    selected = materialize._source(
        case.broker, case.source, allow_canonical=True,
    )
    assert selected[0] is account and selected[1] is case.target
    preview = case.broker.prepare_local_materialization(case.source)
    if mode == "canonical":
        assert preview.ok and preview.facts["status"] == "prepared"
        assert preview.facts["acquisition_authorized"] is False
        assert rig.calls == []
    else:
        assert not preview.ok
        assert preview.facts["status"] == "reconciliation_required"
    assert not case.downloaded
