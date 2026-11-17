"""Legacy materialization must not reattribute completed bytes to a new owner."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.materialization import MaterializationCoordinator
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_6560_scoped_holdings_read import row


def test_legacy_materialization_publishes_exact_captured_coordinates(tmp_path):
    item = row()
    path = tmp_path / "book.epub"
    path.write_bytes(b"synthetic")
    calls = []
    owner = NS(identity_id="alice")
    result = MaterializationCoordinator(
        acquire=lambda actual: NS(
            path=path, provider_id=actual.source_provider,
            credential_id=actual.source_credential, item_id=actual.record_id,
        ),
        resolve_identity=lambda *_: owner,
        index_path=lambda p, **kw: calls.append((p, kw)) or {
            "status": "indexed", "artifact_id": "synthetic",
        },
        source_epoch=lambda: 7,
    ).obtain(item)
    assert result.identity_id == "alice"
    assert calls == [(path, dict(title=item.title, item_id=item.record_id,
                                 identity_id="alice", provider_id=item.source_provider))]


@pytest.mark.parametrize("change", ["owner", "epoch", "item", "account", "receipt"])
def test_changed_binding_or_receipt_never_indexes_downloaded_bytes(tmp_path, change):
    item = row()
    path = tmp_path / "book.epub"
    owner = NS(identity_id="alice")
    epoch = [7]

    def acquire(actual):
        assert actual is item
        path.write_bytes(b"synthetic")
        result = NS(path=path, provider_id=actual.source_provider,
                    credential_id=actual.source_credential, item_id=actual.record_id)
        if change == "owner":
            owner.identity_id = "bob"
        elif change == "epoch":
            epoch[0] += 2
        elif change == "item":
            actual.record_id = "other"
        elif change == "account":
            actual.source_account_id = "other"
        else:
            result.credential_id = "foreign"
        return result

    with pytest.raises(ProviderConfigurationError, match="materialization_source_revoked"):
        MaterializationCoordinator(
            acquire=acquire, resolve_identity=lambda *_: owner,
            source_epoch=lambda: epoch[0],
            index_path=lambda *_a, **_kw: pytest.fail("wrong owner publication"),
        ).obtain(item)
    # This guard cannot roll back already downloaded bytes.
    assert path.exists()


def test_unproven_owner_refused_before_download():
    with pytest.raises(ProviderConfigurationError, match="materialization_owner_unproven"):
        MaterializationCoordinator(
            acquire=lambda *_: pytest.fail("no supplier dispatch"),
            resolve_identity=lambda *_: None,
            index_path=lambda *_a, **_kw: pytest.fail("no index"),
        ).obtain(row())
