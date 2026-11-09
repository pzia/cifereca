"""P0 #4858: retired W2 has no executable provider/legacy mutation path."""
from __future__ import annotations

import pytest

from cifereca.core.kobo_backup import _sync_kobo_backup_locked
from cifereca.providers import ProviderConfigurationError


class _NoLegacyAccess:
    """Any attempt to resolve historical sessions/storage or execute GET is fatal."""

    def __getattr__(self, name):
        pytest.fail(f"retired W2 accessed {name}")


@pytest.mark.parametrize("credential_id,confirmed_manifest", [
    (None, None),
    ("vault-1", None),
    ("vault-1", []),
    ("vault-1", [{"product_id": "book-1", "downloadable": True}]),
])
def test_w2_refuses_before_legacy_read_write_or_get(credential_id, confirmed_manifest):
    with pytest.raises(ProviderConfigurationError, match="kobo_legacy_w2_retired_use_canonical_provider"):
        _sync_kobo_backup_locked(
            _NoLegacyAccess(), logger=_NoLegacyAccess(),
            credential_id=credential_id, confirmed_manifest=confirmed_manifest,
            progress=lambda *_args, **_kwargs: pytest.fail("unexpected progress"),
            source_guard=lambda: pytest.fail("unexpected source guard"),
            item_guard=lambda *_args: pytest.fail("unexpected item guard"),
        )


def test_w2_refuses_even_when_cancellation_requested():
    with pytest.raises(ProviderConfigurationError, match="kobo_legacy_w2_retired_use_canonical_provider"):
        _sync_kobo_backup_locked(
            _NoLegacyAccess(), logger=_NoLegacyAccess(),
            cancelled=lambda: pytest.fail("unexpected cancellation callback"),
        )
