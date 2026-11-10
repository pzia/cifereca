"""#3196 D1/W2: a fresh Kobo manifest cannot adopt another borrower's 0.7 path.

Synthetic SQLite/files and provider rights manifest only; no content GET/ADEPT.
"""
from __future__ import annotations

import hashlib

import pytest

from tests.test_kobo_backup_fault_isolation import _service, _write_epub
from tests.test_kobo_backup_preview import _forbid_mutation_and_acquisition
from cifereca.core.kobo_backup import kobo_backup_preview


def _rig(tmp_path, *, owner, borrower, archived=False):
    manifest = [{
        "product_id": "book", "revision_id": "rev-1",
        "title": "Synthetic", "format": "EPUB",
        "owned": True, "downloadable": True,
        **({"archived": True} if archived else {}),
    }]
    service, session = _service(tmp_path, manifest)
    path = service.library_identity_provider_dir("default", "kobo") / "book.epub"
    _write_epub(path)
    service.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "identity_id": owner, "account_id": borrower,
        "item_id": "book", "revision_id": "rev-1",
        "title": "Synthetic", "format": "EPUB", "status": "complete",
        "local_path": str(path),
        "size": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    })
    return service, session, path



@pytest.mark.parametrize("owner,borrower", [
    ("default", "former-borrower"),
    ("other-profile", "account"),
    (None, "account"),
    ("default", None),
    ("default", "account"),
])
@pytest.mark.parametrize("archived", [False, True])
def test_old_borrower_and_archive_never_grant_private_w2_presence(
    tmp_path, monkeypatch, owner, borrower, archived,
):
    from cifereca.providers import ProviderConfigurationError
    service, session, path = _rig(
        tmp_path, owner=owner, borrower=borrower, archived=archived,
    )
    before = service.storage.backup_get("kobo", "vault-1", "book")
    _forbid_mutation_and_acquisition(service, session, monkeypatch)
    monkeypatch.setattr(session, "backup_manifest",
                        lambda: pytest.fail("no legacy manifest read"))
    try:
        with pytest.raises(ProviderConfigurationError,
                           match="canonical_kobo_preview_abi_unavailable"):
            kobo_backup_preview(
                service, "vault-1", refresh_manifest=True,
                verify_local_files=True, _item_reasons={},
            )
        assert service.storage.backup_get("kobo", "vault-1", "book") == before
        assert path.is_file()
    finally:
        service.close()
