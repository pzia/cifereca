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
