"""#3196: offline relink uses exact provider scope, without Kobo-only Core facade."""
from __future__ import annotations

import json
import pytest

from cifereca.core.offline_canonical_relink import (
    ReconciliationRejected, reconcile_existing_profile,
)
from cifereca.core.canonical_relink import CanonicalRelinkRejected
from tests.test_core_p0_3171_atomic_publication_v14 import (
    store, new_source, register, target, publish, BYTES,
)  # noqa: F401


def test_generic_offline_relink_requires_exact_provider_and_preserves_file(
    store, tmp_path, monkeypatch,
):
    from cifereca.core import canonical_relink

    monkeypatch.setattr(
        canonical_relink, "acquisition_lock_file",
        lambda: tmp_path / "generic-relink.lock",
    )
    key, snapshot = new_source(
        "A", provider="synthetic-seller", credential="card-a",
        account="buyer-a", edition="edition-a", right="purchase",
    )
    register(store, "A", "synthetic-seller", "card-a", account="buyer-a")
    root = tmp_path / "EBooks"
    old_path = target(tmp_path, "A", "original.pdf")
    published = publish(store, root, old_path, key, snapshot)
    new_path = old_path.with_name("relocated.pdf")
    old_path.rename(new_path)
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({
        "identities": [{"id": "A", "directory_name": "A", "label": "A"}],
        "library": {"directory": str(root)},
    }), encoding="utf-8")
    args = {
        "config_path": profile, "database_path": store.path,
        "canonical_relink": True, "provider_id": "synthetic-seller",
        "identity_id": "A", "credential_id": "card-a",
        "account_id": "buyer-a", "edition_id": "edition-a",
        "right_id": "purchase", "branch_id": "purchases",
        "placement_id": published.placement_id, "path": str(new_path),
    }
    def immutable_rows():
        with store._lock, store._connect() as db:
            return tuple(
                tuple(db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall())
                for table in ("source_references", "origin_links", "artifacts",
                              "entitlements", "backup_items")
            )

    before = immutable_rows()
    with pytest.raises(CanonicalRelinkRejected):
        reconcile_existing_profile(**{**args, "provider_id": "kobo"})
    with pytest.raises(ReconciliationRejected, match="invalid_request"):
        reconcile_existing_profile(**{**args, "provider_id": ""})
    preview = reconcile_existing_profile(**args)
    assert preview["status"] == "preview"
    assert preview["schema"] == "core.canonical-placement-relink/1"
    assert old_path.exists() is False and new_path.read_bytes() == BYTES
    confirmed = reconcile_existing_profile(
        **{**args, "confirmation": preview["plan_token"]},
    )
    assert confirmed["status"] == "relinked"
    assert confirmed["changed"] == 1
    assert confirmed["file_mutations"] == 0
    assert confirmed["rights_mutations"] == 0
    assert store.placement_get(published.placement_id)["path"] == str(new_path)
    assert before == immutable_rows()
    assert old_path.exists() is False and new_path.read_bytes() == BYTES
