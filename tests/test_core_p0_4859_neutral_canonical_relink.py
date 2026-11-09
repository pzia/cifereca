"""#4859: canonical B relink is provider-neutral; Kobo is only a compatibility facade."""
from cifereca.core.canonical_placement_relink import (
    CanonicalRelinkRejected, relink_verified_canonical,
)
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, store, new_source, register, target, publish,
)
import pytest


def test_future_provider_relinks_only_existing_attested_b(store, tmp_path):
    source, snapshot = new_source(
        profile="A", provider="future-lender", credential="card-A",
        account="buyer-A", edition="future-book", right="owned",
    )
    register(store, "A", "future-lender", "card-A", account="buyer-A")
    old = target(tmp_path, "A", "future.pdf")
    completed = publish(store, tmp_path / "EBooks", old, source, snapshot)
    assert completed.changed
    new = old.with_name("relocated.pdf")
    old.rename(new)
    args = dict(
        provider_id="future-lender", lock_path=tmp_path / "relink.lock",
        library_root=tmp_path / "EBooks", identity_id="A",
        credential_id="card-A", account_id="buyer-A",
        edition_id="future-book", right_id="owned",
        branch_id="purchases", placement_id=completed.placement_id,
        new_path=new,
    )
    # A different supplier's exact SQL source cannot claim this proof.
    with pytest.raises(CanonicalRelinkRejected):
        relink_verified_canonical(store, **dict(args, provider_id="kobo"))
    preview = relink_verified_canonical(store, **args)
    assert preview["schema"] == "core.future-lender.canonical-placement-relink/1"
    assert preview["status"] == "preview"
    assert preview["changed"] == 0
    done = relink_verified_canonical(
        store, confirmation=preview["plan_token"], **args,
    )
    assert done["status"] == "relinked" and done["changed"] == 1
    assert done["rights_mutations"] == done["file_mutations"] == 0
    placement = store.placement_get(completed.placement_id)
    assert placement["path"] == str(new)
    assert new.read_bytes() == BYTES
    assert not old.exists()
