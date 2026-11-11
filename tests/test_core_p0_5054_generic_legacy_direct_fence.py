"""#5054: the old direct GET cannot deliver a selected private source.

Only canonical selected-offer acquisition can verify session, consent, bytes,
artifact SHA and placement. This test imports no supplier implementation.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.models import SearchResult
from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService


def _item(**changes):
    item = SearchResult(
        id="private-item", record_id="private-item",
        docbase="FUTURE", medium="digital", title="Synthetic private title",
        author=None, type="ebook", format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="PRIVATE_PURCHASES",
        source_provider="future-store", source_credential="credential-A",
        source_collection="library", owned=True, downloadable=True,
        content_type="ebook",
    )
    return replace(item, **changes)


def _service(session=None):
    def forbidden(*_args, **_kwargs):
        pytest.fail("legacy path must not resolve a supplier or touch files")
    return NS(
        _sessions={"future-store:credential-A": session} if session is not None else {},
        _session_key=lambda provider, cred: f"{provider}:{cred}",
        _session_for_source=forbidden,
        acquisition=NS(content_type_for=forbidden, override_command=forbidden),
        identity_for_connection=forbidden,
        library_identity_provider_dir=forbidden,
        diagnostics=NS(add=forbidden),
    )


@pytest.mark.parametrize("session,item", [
    (NS(selected_purchase_branch="selected"), _item()),
    (NS(selected_purchase_branch=""), _item()),
    (NS(private_inventory_row_contract=("library", "PRIVATE_PURCHASES", "credential_scoped_record_v1")), _item()),
    (NS(selected_purchase_branch="selected"), _item(source_collection="public_catalog", owned=False)),
    (NS(selected_purchase_branch="selected"), _item(source_credential="credential-A", download_url="https://example.invalid/file")),
    (None, _item()),
])
def test_no_supplier_specific_selected_or_private_row_can_use_direct_get(
    session, item,
):
    service = _service(session)
    with pytest.raises(
        ProviderConfigurationError, match="canonical_explicit_action_required",
    ):
        LibraryService._acquire_direct_item_impl(service, item)


def test_unsupported_targeted_admission_is_denied_before_supplier_resolution():
    service = _service()
    with pytest.raises(ProviderConfigurationError, match="autorisation du compte"):
        LibraryService._acquire_direct_item_impl(
            service, _item(source_collection="public_catalog"),
            _admission=lambda: True,
        )


def test_non_private_legacy_offer_remains_on_its_existing_path():
    service = _service()
    def reached(*_args, **_kwargs):
        raise RuntimeError("existing legacy path")
    service._session_for_source = reached
    with pytest.raises(RuntimeError, match="existing legacy path"):
        LibraryService._acquire_direct_item_impl(
            service, _item(
                source_collection="public_catalog", owned=False,
                download_url="https://example.invalid/epub",
            ),
        )
