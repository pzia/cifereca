"""#5054: canonical Numilog sessions cannot use the historical direct GET writer.

Synthetic provider and real SQLite; no live supplier request or receipt.
"""
import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_numilog_owned_library_download import case


_CANONICAL = (
    "downloadable_resource_snapshot",
    "downloadable_selected_offer_snapshot",
    "downloadable_selected_delivery_candidate_guarded",
)


def _become_canonical(session):
    for name in _CANONICAL:
        setattr(session, name, lambda *_a, **_k: None)


def test_v2_complete_refuses_legacy_direct_before_any_get_or_writer(case):
    _become_canonical(case.session)
    with pytest.raises(
        ProviderConfigurationError, match="numilog_canonical_source_delivery_active",
    ):
        case.service.acquire_direct_item(case.item)
    assert case.calls == []
    assert case.service.storage.acquisitions(provider_id="numilog") == []


def test_same_session_v2_upgrade_during_admission_refuses_before_get(case):
    def upgrade():
        _become_canonical(case.session)
        return case.item

    with pytest.raises(
        ProviderConfigurationError, match="numilog_canonical_source_delivery_active",
    ):
        case.service.acquire_direct_item(case.item, _admission=upgrade)
    assert case.calls == []
    assert case.service.storage.acquisitions(provider_id="numilog") == []


def test_upgrade_during_legacy_get_cannot_create_v1_receipt(case):
    previous = case.session.download_direct
    def upgrade_after_get(item, target):
        delivered = previous(item, target)
        _become_canonical(case.session)
        return delivered

    case.session.download_direct = upgrade_after_get
    with pytest.raises(
        ProviderConfigurationError, match="numilog_canonical_source_delivery_active",
    ):
        case.service.acquire_direct_item(case.item)
    assert case.calls == [case.item.record_id]
    # Uncertain bytes cannot be committed as historical v1 authority.
    assert case.service.storage.acquisitions(provider_id="numilog") == []


def test_partial_v2_contract_never_claims_canonical_execution(case):
    for name in _CANONICAL[:-1]:
        setattr(case.session, name, lambda *_a, **_k: None)
    acquired = case.service.acquire_direct_item(case.item)
    assert acquired.metadata["numilog_checkpoint_id"].startswith(
        "numilog-materialization:"
    )
    assert case.calls == [case.item.record_id]
