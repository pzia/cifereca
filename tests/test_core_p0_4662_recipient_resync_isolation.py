"""#4662: a missing recipient copy cannot authorize provider restoration.

Real local transfer, SQLite and canonical source Sync; synthetic PDF,
provider accounts and observed rights only. No network or DRM processing.
"""
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.core.source_acquisition_consent import (
    read_source_acquisition_consent,
    set_source_acquisition_consent,
)
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.models import Account
from cifereca.services import LibraryService
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register,
)
from tests.test_core_p0_3171_canonical_sync_stage import Service as SyncHarness
from tests.test_core_p1_3275_live_canonical_transfer import prepare


def scoped_sync(db, root, identity, credential, account_id):
    harness = SyncHarness(db, root)
    account = Account(identity_id=identity, provider_id="seller-a",
                      credential_id=credential, unique_id=account_id)
    harness.account = account
    harness.accounts = [account]
    harness.session.credential_id = credential
    harness.session.accounts = [account]
    harness._sessions = {harness._session_key(account.provider_id, credential):
                         harness.session}
    harness.source, harness.snapshot = new_source(
        identity, provider=account.provider_id, credential=credential,
        account=account_id, branch="loans", edition="same-edition",
        right="same-loan-reference", kind="loan",
    )
    harness._canonical_persisted_consent = (
        lambda account: LibraryService._canonical_persisted_consent(harness, account)
    )
    harness._canonical_right_delivery = lambda *_: pytest.fail(
        "recipient history must not trigger provider delivery"
    )
    return harness


def test_missing_recipient_copy_and_two_syncs_never_inherit_lender_right(
    tmp_path, monkeypatch,
):
    service, root, donor, placement, _ = prepare(tmp_path)
    db = service.storage
    lock = tmp_path / "isolated-acquisition.lock"
    for module in ("cifereca.paths", "cifereca.core.canonical_transfer",
                   "cifereca.core.sync_coordination",
                   "cifereca.core.convergence_execution",
                   "cifereca.core.canonical_recovery"):
        monkeypatch.setattr(module + ".acquisition_lock_file", lambda: lock)
    try:
        register(db, "alice", "seller-a", "card-A", account="borrower-A")
        register(db, "bob", "seller-a", "card-B", account="borrower-B")
        lender = scoped_sync(db, root, "alice", "card-A", "borrower-A")
        recipient = scoped_sync(db, root, "bob", "card-B", "borrower-B")
        publish(db, root, donor, lender.source, lender.snapshot)
        set_source_acquisition_consent(
            lender, lender.account, lender.source, allowed=True,
        )
        assert read_source_acquisition_consent(lender, lender.account) == {lender.source}
        sent = service.materialize_to_identity(placement, "bob")
        assert sent["status"] == "complete"
        copied = Path(sent["path"])
        assert copied.read_bytes() == donor.read_bytes() == BYTES
        assert not copied.samefile(donor)
        # The same edition and loan ID in a different account are deliberately
        # not an observed right. Recipient history cannot fill this absence.
        recipient.snapshot = replace(recipient.snapshot, rights=())
        with pytest.raises(ValueError, match="positive_source_required"):
            set_source_acquisition_consent(
                recipient, recipient.account, recipient.source, allowed=True,
            )
        assert read_source_acquisition_consent(recipient, recipient.account) == frozenset()
        before_rights = db.entitlements()
        before_ledger = db.acquisitions()
        copied.unlink()  # only the fixture's recipient file, never production
        for _ in range(2):
            result = synchronize_account_source(recipient, recipient.account)
            assert result["canonical_rights"]["observed"] == 0
            assert result["canonical_rights"]["attested_active"] == 0
            assert result.get("canonical_execution", {}).get("downloaded", 0) == 0
            assert not copied.exists()
            assert donor.read_bytes() == BYTES
            assert read_source_acquisition_consent(recipient, recipient.account) == frozenset()
        assert recipient.read_calls == 2  # explicit refresh still reads source
        assert db.entitlements() == before_rights
        assert db.acquisitions() == before_ledger
        with db._lock, db._connect() as sql:
            assert sql.execute(
                "SELECT identity_id,reference_kind,synchronizable "
                "FROM source_references ORDER BY identity_id"
            ).fetchall() == [("alice", "loan", 1), ("bob", "internal", 0)]
        assert read_source_acquisition_consent(lender, lender.account) == {lender.source}
    finally:
        service.close()
