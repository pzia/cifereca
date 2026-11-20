"""#4122: actual Numilog adapter + explicit Library click + canonical writer.

Reuse the existing six-purchase fixture, mock HTTPS and ADEPT boundary only.
No production paths, accounts, supplier contact or automatic consent change.
"""
from pathlib import Path
from hashlib import sha256

import pytest

from cifereca.catalog import LocalCatalog
from cifereca.core.owned_library_download import OwnedDownloadRejected
from tests.test_six_purchase_core_conformance import purchases, attest, content_gets, epub_bytes


def _target(service, account, key):
    service.select_identity(account.identity_id)
    assert key.edition_id == key.right_id
    return service.owned_library_download_target({"possessions": [{
        "identity_id": account.identity_id,
        "provider_id": account.provider_id,
        "credential_id": account.credential_id,
        "account_id": account.unique_id,
        "item_id": key.edition_id, "kind": "owned", "state": "active",
    }]})


@pytest.mark.parametrize("existing", ["exact", "unlinked", "absent"])
def test_explicit_library_acquisition_is_canonical_and_does_not_redownload(
    purchases, tmp_path, monkeypatch, existing,
):
    (service, session, account, snapshot, keys, paths, urls,
     gets, root, kind, runtime_calls) = purchases
    lock = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.explicit_owned_canonical.acquisition_lock_file",
                        lambda: lock)
    selected = 3
    for i, (key, path) in enumerate(zip(keys, paths)):
        if i != selected or existing == "exact":
            attest(service, snapshot, key, path, root)
        elif existing == "unlinked":
            assert LocalCatalog(service.storage, root).index_path(
                path, identity_id=account.identity_id,
            )["status"] == "indexed"
        else:
            # Delete only a fresh synthetic fixture copy, never a real purchase.
            path.unlink()
    originals = {p: p.read_bytes() for p in paths if p.exists()}
    digest = sha256((paths[selected].read_bytes() if paths[selected].exists()
                     else epub_bytes(selected))).hexdigest()
    before = list(gets)
    target = _target(service, account, keys[selected])
    assert target is not None and gets == before
    admitted = service.capture_admitted_owned_library_offer(target)
    assert gets == before
    result = service.obtain_admitted_owned_library_offer(admitted)
    assert result.artifact_id == "sha256:" + digest
    delivered = result.path
    assert isinstance(delivered, Path) and delivered.is_file()
    assert sha256(delivered.read_bytes()).hexdigest() == digest
    if existing == "exact":
        assert delivered == paths[selected]
    else:
        assert delivered.parent == root / "hugues" / "Acquisitions"
    expected_gets = [] if existing == "exact" else [urls[selected]]
    assert content_gets(gets, urls) == expected_gets
    assert len(runtime_calls) == int(kind == "acsm" and existing != "exact")
    before_placements = service.storage.placements()
    # A fresh explicit request from a previously displayed remote card is safe;
    # actual canonical presence, not the stale UI hint, suppresses another GET.
    target = _target(service, account, keys[selected])
    assert target is not None
    again = service.obtain_admitted_owned_library_offer(
        service.capture_admitted_owned_library_offer(target))
    assert again.artifact_id == result.artifact_id
    assert content_gets(gets, urls) == expected_gets
    assert service.storage.placements() == before_placements
    assert {p: p.read_bytes() for p in originals} == originals
    with service.storage._lock, service.storage._connect() as db:
        assert db.execute(
            "SELECT count(*) FROM origin_links WHERE origin_kind='provider'"
        ).fetchone()[0] == 6


def test_library_click_cannot_survive_source_epoch_revocation(purchases, tmp_path, monkeypatch):
    service, session, account, snapshot, keys, paths, urls, gets, root, kind, runtime_calls = purchases
    lock = tmp_path / "locks" / "acquisition.lock"
    monkeypatch.setattr("cifereca.core.explicit_owned_canonical.acquisition_lock_file",
                        lambda: lock)
    target = _target(service, account, keys[0])
    assert target is not None
    admitted = service.capture_admitted_owned_library_offer(target)
    service._source_context_epoch += 1
    before = list(gets)
    with pytest.raises(OwnedDownloadRejected):
        service.obtain_admitted_owned_library_offer(admitted)
    assert gets == before and not runtime_calls
    assert not service.storage.placements()
