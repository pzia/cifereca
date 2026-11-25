"""#4496: keeper preference is advisory, SHA/profile verified, never destructive."""
from __future__ import annotations

import pytest

from cifereca.core.canonical_keeper_review import review_canonical_keeper
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, publish, register, rows, store, target,
)


def setup_copies(store, tmp_path, names, *, identity="A"):
    root = tmp_path / "EBooks"
    result = []
    for index, name in enumerate(names):
        provider, credential = f"seller-{index}", f"card-{index}"
        source, snapshot = new_source(
            identity, provider=provider, credential=credential,
            account=f"buyer-{index}",
        )
        register(store, identity, provider, credential, account=f"buyer-{index}")
        result.append(publish(
            store, root, target(tmp_path, identity, name), source, snapshot,
        ))
    return root, result


def check(store, root, *, identity="A"):
    return review_canonical_keeper(
        store, library_root=root, identity_id=identity,
        artifact_id="sha256:" + SHA,
    )


def test_prefer_readable_classified_over_opaque_acquisition(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA}.pdf",
        "Livres/Auteur/Autrice - Roman.pdf",
    ))
    before = rows(store)
    result = check(store, root)
    assert (result.status, result.reason, result.executable) == (
        "review_required", "aesthetic_preference_only", False,
    )
    assert result.keeper_placement_id == pair[1].placement_id
    assert result.candidate_placement_ids == (pair[0].placement_id,)
    assert (result.eligible_copies, result.excluded_copies) == (2, 0)
    assert rows(store) == before
    assert all(store.placement_get(x.placement_id)["state"] == "present"
               for x in pair)


def test_named_acquisition_beats_unnamed_classified_path(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/Autrice - Livre.pdf",
        f"Livres/Auteur/{SHA}.pdf",
    ))
    result = check(store, root)
    assert result.status == "review_required"
    assert result.keeper_placement_id == pair[0].placement_id


@pytest.mark.parametrize("prefix", ["Livre", "Autrice - Livre", "Autrice - Saga - 2 - Livre"])
def test_readable_name_with_receipt_suffix_beats_opaque_copy(store, tmp_path, prefix):
    receipt = "a" * 64
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{prefix} - {receipt}.pdf",
        f"Livres/Auteur/{SHA}.pdf",
    ))
    before = rows(store)
    result = check(store, root)
    assert result.status == "review_required"
    assert result.keeper_placement_id == pair[0].placement_id
    assert result.candidate_placement_ids == (pair[1].placement_id,)
    assert result.executable is False
    assert rows(store) == before


def test_two_hash_components_do_not_count_as_a_readable_name(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA} - {'a' * 64}.pdf",
        "Acquisitions/Autrice - Livre.pdf",
    ))
    result = check(store, root)
    assert result.status == "review_required"
    assert result.keeper_placement_id == pair[1].placement_id


def test_equal_rank_is_ambiguous_not_decided_by_placement_id(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        "Livres/Autrice/Autrice - Livre 1.pdf",
        "Livres/Autrice/Autrice - Livre 2.pdf",
    ))
    result = check(store, root)
    assert (result.status, result.reason) == (
        "blocked", "ambiguous_keeper_preference",
    )
    assert not result.keeper_placement_id
    assert not result.candidate_placement_ids


def test_no_cross_profile_and_foreign_identity_cannot_select(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/Un - Livre.pdf",
        "Livres/Un/Un - Livre.pdf",
    ))
    foreign_source, snap = new_source(
        "B", provider="foreign", credential="foreign", account="B",
    )
    register(store, "B", "foreign", "foreign", account="B")
    third = publish(
        store, root, target(tmp_path, "B", "Livres/B/B - Livre.pdf"),
        foreign_source, snap,
    )
    result = check(store, root)
    assert result.status == "review_required"
    assert third.placement_id not in (result.keeper_placement_id,
                                      *result.candidate_placement_ids)
    other = check(store, root, identity="B")
    assert (other.status, other.reason) == (
        "blocked", "insufficient_ordinary_copies",
    )
    assert check(store, root, identity="unknown").status == "blocked"


def test_outbox_is_excluded_even_if_it_is_a_named_book(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA}.pdf",
        "Livres/A/Autrice - Livre.pdf",
    ))
    outbox = target(tmp_path, "A", "Outbox/reader/Autrice - Livre.pdf")
    place = store.placement_put({
        "artifact_id": pair[0].artifact_id, "path": str(outbox),
        "identity_id": "A", "provider_id": None,
        "source_item_id": None, "state": "present", "metadata": {},
    })
    result = check(store, root)
    assert result.status == "review_required"
    assert result.excluded_copies == 1
    assert place not in (result.keeper_placement_id, *result.candidate_placement_ids)
    assert outbox.read_bytes() == BYTES


def test_active_reference_and_unknown_dependency_never_recommended(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA}.pdf",
        "Livres/A/Autrice - Livre.pdf",
    ))
    first_path = root / "A" / "Acquisitions" / f"{SHA}.pdf"
    store.catalog_entry_put({
        "catalog_entry_id": "one", "artifact_id": pair[0].artifact_id,
        "placement_id": pair[0].placement_id, "identity_id": "A",
        "provider_id": "seller-0", "item_id": "edition", "title": "Test",
        "metadata": {"path": str(first_path)},
    })
    assert check(store, root).reason == "candidate_not_safe_for_retirement"
    store.metadata_set("other.owner.path", str(first_path))
    assert check(store, root).reason == "candidate_not_safe_for_retirement"


def test_tamper_or_symlink_refuses_without_mutation(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA}.pdf",
        "Livres/A/Autrice - Livre.pdf",
    ))
    victim = root / "A" / "Acquisitions" / f"{SHA}.pdf"
    victim.write_bytes(BYTES + b"!")
    assert check(store, root).reason == "candidate_not_safe_for_retirement"
    victim.unlink()
    victim.symlink_to(root / "A" / "Livres" / "A" / "Autrice - Livre.pdf")
    assert check(store, root).reason == "excluded_or_unverified_path"


def test_legacy_placement_and_invalid_scope_block(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA}.pdf",
        "Livres/A/Autrice - Livre.pdf",
    ))
    with store._lock, store._connect() as db:
        db.execute(
            "UPDATE placements SET provider_id=? WHERE placement_id=?",
            ("legacy", pair[0].placement_id),
        )
    assert check(store, root).reason == "noncanonical_placement"
    assert review_canonical_keeper(
        store, library_root=root, identity_id="A",
        artifact_id="sha256:BAD",
    ).reason == "invalid_scope"


def test_already_single_copy_yields_no_automatic_deletion(store, tmp_path):
    root, pair = setup_copies(store, tmp_path, ("Acquisitions/A - Livre.pdf",))
    result = check(store, root)
    assert (result.status, result.reason, result.executable) == (
        "blocked", "insufficient_ordinary_copies", False,
    )
    assert store.placement_get(pair[0].placement_id)["state"] == "present"


@pytest.mark.parametrize("reader", ["keeper", "pair"])
def test_advice_never_settles_caller_transaction(store, tmp_path, reader):
    from cifereca.core.canonical_dedup_preview import preview_canonical_duplicate
    root, pair = setup_copies(store, tmp_path, (
        f"Acquisitions/{SHA}.pdf",
        "Livres/Auteur/Autrice - Roman.pdf",
    ))
    def review():
        if reader == "keeper":
            return check(store, root)
        return preview_canonical_duplicate(
            store, library_root=root, candidate_placement_id=pair[0].placement_id,
            keeper_placement_id=pair[1].placement_id,
        )

    db = store._connect()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", ("synthetic.4496", "pending"))
        result = review()
        assert (result.status, result.reason) == ("blocked", "nested_transaction")
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key='synthetic.4496'").fetchone() == ("pending",)
        db.rollback()
        assert store.metadata_get("synthetic.4496") is None
        assert review().status == ("review_required" if reader == "keeper" else "unreferenced")
        assert all(store.placement_get(x.placement_id)["state"] == "present" for x in pair)
    finally:
        if db.in_transaction:
            db.rollback()


@pytest.mark.parametrize("enough", [True, False])
def test_keeper_charges_repeated_hashes_before_any_pair_preview(store, tmp_path, monkeypatch, enough):
    from cifereca.core import canonical_keeper_review as module
    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/first.pdf", "Acquisitions/second.pdf",
        "Livres/Auteur/Autrice - Roman.pdf",
    ))
    # Two candidates, each pair reads 2 * size, even though all 3 copies
    # together would fit a smaller budget.
    monkeypatch.setattr(module, "_MAX_HASH_BYTES", len(BYTES) * 4 - (not enough))
    original = module.preview_canonical_duplicate
    calls = []
    def preview(*args, **kwargs):
        calls.append(kwargs["candidate_placement_id"])
        return original(*args, **kwargs)
    monkeypatch.setattr(module, "preview_canonical_duplicate", preview)
    before = rows(store)
    result = check(store, root)
    assert result.status == ("review_required" if enough else "blocked")
    assert len(calls) == (2 if enough else 0)
    if not enough:
        assert result.reason == "physical_budget"
    assert rows(store) == before and not result.executable


@pytest.mark.parametrize("mutate", ["same_bytes_new_inode", "missing", "keeper_changed"])
def test_earlier_copy_change_after_pair_read_revokes_entire_keeper_advice(store, tmp_path, monkeypatch, mutate):
    from cifereca.core import canonical_keeper_review as module
    from pathlib import Path
    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/first.pdf", "Acquisitions/second.pdf",
        "Livres/Auteur/Autrice - Roman.pdf",
    ))
    original = module.preview_canonical_duplicate
    seen = []
    def preview(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.status == "unreferenced"
        seen.append(kwargs["candidate_placement_id"])
        if len(seen) == 2:
            pid = kwargs["keeper_placement_id"] if mutate == "keeper_changed" else seen[0]
            victim = Path(store.placement_get(pid)["path"])
            victim.rename(victim.with_suffix(".old"))
            if mutate != "missing":
                victim.write_bytes(BYTES)
        return result
    monkeypatch.setattr(module, "preview_canonical_duplicate", preview)
    before = rows(store)
    result = check(store, root)
    assert (result.status, result.reason) == ("blocked", "physical_evidence_changed")
    assert len(seen) == 2 and not result.executable
    assert not result.keeper_placement_id and not result.candidate_placement_ids
    assert rows(store) == before


@pytest.mark.parametrize("dependency", ["metadata", "catalogue"])
def test_keeper_refuses_dependency_committed_after_last_pair_review(
    store, tmp_path, monkeypatch, dependency,
):
    from cifereca.core import canonical_keeper_review as module

    root, pair = setup_copies(store, tmp_path, (
        "Acquisitions/first.pdf", "Acquisitions/second.pdf",
        "Livres/Auteur/Autrice - Roman.pdf",
    ))
    original = module.preview_canonical_duplicate
    seen = []
    paths = tuple(store.placement_get(p.placement_id)["path"] for p in pair)

    def preview(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.status == "unreferenced"
        seen.append(kwargs["candidate_placement_id"])
        if len(seen) == 2:
            # Introduce a dependency for the earlier candidate only after
            # both pair-specific checks have finished.
            victim = store.placement_get(seen[0])
            if dependency == "metadata":
                store.metadata_set("synthetic.keeper.checkpoint", victim["path"])
            else:
                store.catalog_entry_put({
                    "catalog_entry_id": "late-dependency",
                    "placement_id": victim["placement_id"],
                    "artifact_id": victim["artifact_id"],
                    "identity_id": "A", "title": "Late dependency",
                    "metadata": {"path": victim["path"]},
                })
        return result

    monkeypatch.setattr(module, "preview_canonical_duplicate", preview)
    result = check(store, root)
    assert (result.status, result.reason) == ("blocked", "snapshot_changed")
    assert len(seen) == 2 and not result.executable
    assert not result.keeper_placement_id and not result.candidate_placement_ids
    from pathlib import Path
    assert all(Path(p).read_bytes() == BYTES for p in paths)
    assert all(store.placement_get(p.placement_id)["state"] == "present" for p in pair)
