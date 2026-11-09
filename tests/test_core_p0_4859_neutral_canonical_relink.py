"""P0 #4859: relink uses exact canonical evidence, not a Kobo decision.

Real SQLite B publication and a synthetic provider, no supplier GET/legacy
backup or additional rights. The Kobo facade retains its existing tests.
"""
from pathlib import Path
import pytest

from cifereca.core.kobo_canonical_relink import (
    CanonicalRelinkRejected, relink_verified_canonical,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, new_source, register, target, publish, BYTES,
)


@pytest.mark.parametrize("provider", ["synthetic-alpha", "synthetic-beta"])
def test_canonical_relink_for_unrecognized_provider_is_exact(store, tmp_path, monkeypatch, provider):
    from cifereca.core import canonical_relink as implementation
    monkeypatch.setattr(
        implementation, "acquisition_lock_file",
        lambda: tmp_path / "relink.lock",
    )
    source, snapshot = new_source(
        "A", provider=provider, credential="card-A",
        account="borrower-A", edition="edition-A", right="purchase",
    )
    register(store, "A", provider, "card-A", account="borrower-A")
    old = target(tmp_path, "A", "first.pdf")
    published = publish(store, tmp_path / "EBooks", old, source, snapshot)
    moved = old.with_name("moved.pdf")
    old.rename(moved)
    arguments = dict(
        provider_id=provider, library_root=tmp_path / "EBooks",
        identity_id="A", credential_id="card-A", account_id="borrower-A",
        edition_id="edition-A", right_id="purchase", branch_id="purchases",
        placement_id=published.placement_id, new_path=moved,
    )
    with store._lock, store._connect() as db:
        before = {
            name: tuple(db.execute("SELECT * FROM " + name))
            for name in ("source_references", "origin_links", "artifacts")
        }
    with pytest.raises(CanonicalRelinkRejected):
        relink_verified_canonical(store, **{**arguments, "provider_id": "kobo"})
    preview = relink_verified_canonical(store, **arguments)
    assert preview["status"] == "preview" and preview["changed"] == 0
    done = relink_verified_canonical(
        store, **arguments, confirmation=preview["plan_token"],
    )
    assert done["status"] == "relinked" and done["changed"] == 1
    assert store.placement_get(published.placement_id)["path"] == str(moved)
    assert moved.read_bytes() == BYTES
    assert not old.exists()
    with store._lock, store._connect() as db:
        after = {
            name: tuple(db.execute("SELECT * FROM " + name))
            for name in before
        }
    assert before == after
