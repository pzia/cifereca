"""Behavioral counterparts of the source-placement structural contracts."""
import hashlib

import pytest

from tests.test_core_library_placement_identity_scope import _service, _placement


@pytest.mark.parametrize('alteration', [None, 'status', 'identity_id', 'provider_id', 'credential_id', 'item_id', 'missing', 'duplicate'])
def test_source_match_requires_exact_complete_unique_present_acquisition(tmp_path, monkeypatch, alteration):
    service = _service(tmp_path)
    try:
        sha = hashlib.sha256(b'same epub contents').hexdigest()
        artifact = 'sha256:' + sha
        service.storage.artifact_put({'artifact_id': artifact, 'sha256': sha, 'size': 18,
                                      'media_format': 'epub', 'metadata': {}})
        placement = _placement(service, tmp_path, owner='alice', filename='book.epub',
                               artifact_id=artifact, item_id='book')
        acquisition = {'acquisition_id': 'a1', 'identity_id': 'alice', 'provider_id': 'paris',
                       'credential_id': 'paris-a', 'item_id': 'book', 'artifact_id': artifact,
                       'status': 'complete', 'title': 'Identical title'}
        if alteration in {'identity_id', 'provider_id', 'credential_id', 'item_id'}:
            acquisition[alteration] = 'bob' if alteration == 'identity_id' else 'different'
        elif alteration == 'status':
            acquisition['status'] = 'attention_required'
        service.storage.acquisition_put(acquisition)
        if alteration == 'missing':
            (tmp_path / 'alice/book.epub').unlink()
        elif alteration == 'duplicate':
            _placement(service, tmp_path, owner='alice', filename='copy.epub',
                       artifact_id=artifact, item_id='copy')

        def forbidden(*_args, **_kwargs):
            raise AssertionError('read projection must not scan, sync or acquire')

        for name in ['catalog_scan', 'opening_sync', 'acquire_digital_loan', 'search']:
            monkeypatch.setattr(service, name, forbidden)
        selected = service.local_placement_for_source(
            provider_id='paris', credential_id='paris-a', item_id='book', identity_id='alice',
        )
        bulk = service.local_placements_for_sources('alice')
        if alteration is None:
            assert selected['placement_id'] == placement
            assert bulk[('paris', 'paris-a', 'book')]['placement_id'] == placement
        else:
            assert selected is None
            assert ('paris', 'paris-a', 'book') not in bulk
    finally:
        service.close()


@pytest.mark.parametrize("provider", ["kobo", "numilog", "paris", "synthetic-provider"])
def test_archived_purchases_are_display_only_without_supplier_dispatch(provider):
    from cifereca.core.library_projection import _entitlement_visible_in_library

    assert _entitlement_visible_in_library({
        "provider_id": provider, "state": "archived", "entitlement_type": "owned",
    })
    for kind in ("loan", "license", "unknown", None):
        assert not _entitlement_visible_in_library({
            "provider_id": provider, "state": "archived", "entitlement_type": kind,
        })
    assert _entitlement_visible_in_library({
        "provider_id": provider, "state": "active", "entitlement_type": "loan",
    })
    for state in ("expired", "revoked", "returned", "cancelled", None, True, 1):
        assert not _entitlement_visible_in_library({
            "provider_id": provider, "state": state, "entitlement_type": "owned",
        })


def test_archived_display_policy_never_interprets_provider_claim_as_a_right():
    from cifereca.core.library_projection import _entitlement_visible_in_library

    for malformed in (None, True, "archived", [], {"state": "archived"}, {
        "state": "archived", "entitlement_type": "purchase",
    }):
        assert not _entitlement_visible_in_library(malformed)
