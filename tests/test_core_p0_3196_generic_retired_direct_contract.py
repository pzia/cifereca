"""A provider-declared retired direct GET always routes to canonical acquisition.

The generic Core rule is independent of a supplier name, ABI completeness,
manifest capabilities, historical rights, or a forged public download URL.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.providers import ProviderConfigurationError
from cifereca.services import LibraryService
from tests.test_core_p0_1862_numilog_store_mirror import _row


@pytest.mark.parametrize("change", [
    {},
    {"source_collection": "public_catalog", "owned": False,
     "download_url": "https://example.invalid/asset.epub"},
    {"source_collection": "public_catalog", "owned": None,
     "download_url": None},
])
@pytest.mark.parametrize("with_admission", [False, True])
def test_retired_direct_get_provider_neutral_and_before_any_provider_io(
    change, with_admission,
):
    item = replace(
        _row("cred-a", 0), source_provider="future-provider", **change,
    )
    calls = []
    def forbidden(*_a, **_kw):
        calls.append("unexpected")
        pytest.fail("retired direct GET must never resolve a session, rights or URL")
    session = NS(
        legacy_direct_get_retired=True,
        capabilities=NS(direct_download=True),
        download_direct=forbidden,
    )
    service = NS(
        _sessions={"future-provider:cred-a": session},
        _session_key=lambda p, c: f"{p}:{c}",
        _session_for_source=forbidden,
        acquisition=NS(content_type_for=forbidden, override_command=forbidden),
        identity_for_connection=forbidden,
    )
    with pytest.raises(
        ProviderConfigurationError, match="canonical_explicit_action_required",
    ):
        LibraryService._acquire_direct_item_impl(
            service, item,
            _admission=forbidden if with_admission else None,
        )
    assert calls == []


def test_retired_direct_offer_never_sets_legacy_button():
    item = replace(
        _row("cred-a", 1), source_provider="future-provider",
        source_collection="public_catalog", owned=False,
        download_url="https://example.invalid/file.epub",
    )
    session = NS(
        legacy_direct_get_retired=True,
        capabilities=NS(direct_download=True),
    )
    calls = []
    service = NS(
        _sessions={"future-provider:cred-a": session},
        _session_key=lambda p, c: f"{p}:{c}",
        _session_for_source=lambda p, c: calls.append((p, c)) or session,
    )
    assert LibraryService.can_direct_download(service, item) is False
    assert calls == [("future-provider", "cred-a")]


def test_nonretired_public_provider_retains_direct_hint():
    item = replace(
        _row("cred-a", 2), source_provider="independent-public",
        source_collection="public_catalog", owned=False,
        download_url="https://example.invalid/file.epub",
    )
    session = NS(
        capabilities=NS(direct_download=True),
    )
    service = NS(
        _sessions={"independent-public:cred-a": session},
        _session_key=lambda p, c: f"{p}:{c}",
        _session_for_source=lambda *_a: session,
    )
    assert LibraryService.can_direct_download(service, item) is True
