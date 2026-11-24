from types import SimpleNamespace
from unittest.mock import Mock, patch

from cifereca.plugin_api import LegacyRightsResourceAdapter, ProviderSession


def test_adapter_is_opt_in_and_preserves_scope():
    assert not hasattr(ProviderSession, "downloadable_resource_snapshot")
    account = SimpleNamespace(identity_id="alice", provider_id="synthetic",
                              credential_id="cred", unique_id="borrower")
    scope = LegacyRightsResourceAdapter._downloadable_scope(account)
    assert (scope.identity_id, scope.provider_id, scope.credential_id,
            scope.account_id) == ("alice", "synthetic", "cred", "borrower")


def test_adapter_delegates_once_and_preserves_explicit_action():
    adapter = LegacyRightsResourceAdapter()
    adapter.rights_observation_snapshot = Mock()
    adapter.rights_selected_delivery_offer_snapshot = Mock()
    account = object()
    with patch("cifereca.plugin_api.downloadable_resources.from_legacy_rights_snapshot") as project:
        assert adapter.downloadable_resource_snapshot(account) is project.return_value
        adapter.rights_observation_snapshot.assert_called_once_with(account)
        project.assert_called_once_with(adapter.rights_observation_snapshot.return_value)
        project.reset_mock()
        assert adapter.downloadable_selected_offer_snapshot(
            account, edition_id="edition", resource_id="loan", kind="loan",
            branch="synthetic", explicit_user_action=True,
        ) is project.return_value
        adapter.rights_selected_delivery_offer_snapshot.assert_called_once_with(
            account, edition_id="edition", right_id="loan", kind="loan",
            branch="synthetic", explicit_user_action=True,
        )
        project.assert_called_once_with(adapter.rights_selected_delivery_offer_snapshot.return_value)


import pytest
from cifereca.plugin_api import ProviderCapabilityError


@pytest.mark.parametrize("change", ["owner", "invalid_owner", "revoked"])
def test_staged_candidate_cleanup_after_scope_or_admission_change(tmp_path, change):
    adapter = LegacyRightsResourceAdapter()
    account = SimpleNamespace(identity_id="alice", provider_id="synthetic",
                              credential_id="cred", unique_id="borrower")
    staged = tmp_path / "candidate.acsm"
    def transfer(*args, **kwargs):
        staged.write_bytes(b"synthetic")
        if change == "owner":
            account.identity_id = "bob"
        elif change == "invalid_owner":
            account.identity_id = ""
        return staged, "acsm"
    adapter.rights_selected_delivery_candidate = Mock(side_effect=transfer)
    with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
        adapter._stage_legacy_delivery_candidate(
            account, edition_id="edition", resource_id="loan", kind="loan",
            branch="synthetic", selected_offer_id="offer", target_dir=tmp_path,
            admission_check=lambda guard: change != "revoked",
        )
    assert not staged.exists()
    assert adapter.rights_selected_delivery_candidate.call_count == 1


def test_revocation_never_removes_outside_staging(tmp_path):
    adapter = LegacyRightsResourceAdapter()
    account = SimpleNamespace(identity_id="alice", provider_id="synthetic",
                              credential_id="cred", unique_id="borrower")
    target = tmp_path / "stage"
    target.mkdir()
    outside = tmp_path / "outside.acsm"
    outside.write_bytes(b"synthetic")
    adapter.rights_selected_delivery_candidate = Mock(return_value=(outside, "acsm"))
    with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
        adapter._stage_legacy_delivery_candidate(
            account, edition_id="edition", resource_id="loan", kind="loan",
            branch="synthetic", selected_offer_id="offer", target_dir=target,
            admission_check=lambda guard: False,
        )
    assert outside.read_bytes() == b"synthetic"


def test_revocation_preserves_symlink_and_its_target(tmp_path):
    adapter = LegacyRightsResourceAdapter()
    account = SimpleNamespace(identity_id="alice", provider_id="synthetic",
                              credential_id="cred", unique_id="borrower")
    outside = tmp_path / "original.acsm"
    outside.write_bytes(b"synthetic")
    target = tmp_path / "stage"
    target.mkdir()
    link = target / "candidate.acsm"
    link.symlink_to(outside)
    guard = Mock(return_value=False)
    admission_check = Mock(return_value=False)
    adapter.rights_selected_delivery_candidate = Mock(return_value=(link, "acsm"))
    with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
        adapter._stage_legacy_delivery_candidate(
            account, edition_id="edition", resource_id="loan", kind="loan",
            branch="synthetic", selected_offer_id="offer", target_dir=target,
            admission_check=admission_check, admission_current=guard,
            explicit_user_action=True,
        )
    adapter.rights_selected_delivery_candidate.assert_called_once_with(
        account, edition_id="edition", right_id="loan", kind="loan",
        branch="synthetic", delivery_offer_id="offer", target_dir=target,
        explicit_user_action=True, admission_current=guard,
    )
    admission_check.assert_called_once_with(guard)
    assert link.is_symlink()
    assert outside.read_bytes() == b"synthetic"


@pytest.mark.parametrize("branch", ["dilicom", "bnf_html"])
def test_successful_candidate_is_not_publication_authority(tmp_path, branch):
    adapter = LegacyRightsResourceAdapter()
    account = SimpleNamespace(identity_id="alice", provider_id="synthetic",
                              credential_id="cred", unique_id="borrower")
    staged = tmp_path / "candidate.acsm"
    staged.write_bytes(b"synthetic")
    adapter.rights_selected_delivery_candidate = Mock(return_value=(staged, "acsm"))
    candidate = adapter._stage_legacy_delivery_candidate(
        account, edition_id="edition", resource_id="loan", kind="loan",
        branch=branch, selected_offer_id="offer", target_dir=tmp_path,
        admission_check=lambda guard: True,
    )
    assert candidate.source.scope.identity_id == "alice"
    assert candidate.source.branch == branch
    assert candidate.source.resource_id == "loan"
    assert candidate.selected_offer_id == "offer"
    assert candidate.path == staged
    assert candidate.provider_hint == "acsm"
    assert candidate.authorizes_publication is False
    assert staged.read_bytes() == b"synthetic"
    adapter.rights_selected_delivery_candidate.assert_called_once()


def test_aba_scope_restoration_does_not_override_revoked_admission(tmp_path):
    adapter = LegacyRightsResourceAdapter()
    account = SimpleNamespace(identity_id="alice", provider_id="synthetic",
                              credential_id="cred", unique_id="borrower")
    epoch = [0]
    guard = lambda: epoch[0] == 0
    staged = tmp_path / "candidate.acsm"

    def transfer(*args, **kwargs):
        account.identity_id = "bob"
        epoch[0] += 1
        account.identity_id = "alice"
        epoch[0] += 1
        staged.write_bytes(b"synthetic")
        return staged, "acsm"

    adapter.rights_selected_delivery_candidate = Mock(side_effect=transfer)
    with pytest.raises(ProviderCapabilityError, match="selected_delivery_scope_changed"):
        adapter._stage_legacy_delivery_candidate(
            account, edition_id="edition", resource_id="loan", kind="loan",
            branch="synthetic", selected_offer_id="offer", target_dir=tmp_path,
            admission_check=lambda callback: callback(), admission_current=guard,
        )
    assert account.identity_id == "alice"
    assert not staged.exists()
    adapter.rights_selected_delivery_candidate.assert_called_once()
