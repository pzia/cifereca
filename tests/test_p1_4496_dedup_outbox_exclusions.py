"""P1 #4496: identity-scoped Outbox never participates in ordinary dedup.

Real SQLite and synthetic bytes, no removal, file transfer or live USB.
The ordinary cleanup hold remains independent and unchanged.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.canonical_dedup_exclusions import (
    ordinary_dedup_path_excluded, ordinary_dedup_paths_excluded,
)
from cifereca.core.canonical_dedup_preview import preview_canonical_duplicate
from cifereca.core.canonical_post_placement import verified_post_placement_duplicates
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, new_source, publish, register, store, target,
)


def check(path, root, *, identity="A", owner="A", extras=None):
    return ordinary_dedup_path_excluded(
        path, library_root=root, identity_id=identity,
        owner_directory=owner, additional_by_identity=extras,
    )


def test_internal_exclusion_is_scoped_to_registered_owner_and_exact_subtree(tmp_path):
    root = tmp_path / "EBooks"
    (root / "A" / "Acquisitions").mkdir(parents=True)
    (root / "B" / "Acquisitions").mkdir(parents=True)
    assert check(root / "A" / "Outbox" / "device-1" / "book.epub", root)
    assert check(root / "B" / "Outbox" / "device-1" / "book.epub",
                 root, identity="B", owner="B")
    assert not check(root / "A" / "Acquisitions" / "book.epub", root)
    assert not check(root / "A" / "Outbox-backup" / "book.epub", root)
    assert check(root / "B" / "Acquisitions" / "book.epub", root)
    assert not check(root / "B" / "Acquisitions" / "book.epub",
                     root, identity="B", owner="B")
    extras = {"A": ("special-drop", "devices/device-1")}
    assert check(root / "A" / "special-drop" / "book.epub", root, extras=extras)
    assert check(root / "A" / "devices" / "device-1" / "book.epub", root, extras=extras)
    assert not check(root / "A" / "devices" / "device-2" / "book.epub", root, extras=extras)
    assert not check(root / "B" / "special-drop" / "book.epub",
                     root, identity="B", owner="B", extras=extras)


@pytest.mark.parametrize("bad", [
    {"A": ("../escape",)},
    {"A": ("/absolute",)},
    {"A": ("nested//bad",)},
    {"A": ["good", "bad\\path"]},
    {"A": "Outbox"},
    {"A": (42,)},
])
def test_invalid_internal_configuration_fails_closed(tmp_path, bad):
    root = tmp_path / "EBooks"
    assert check(root / "A" / "Acquisitions" / "book.pdf", root, extras=bad)


def test_lexical_traversal_and_symlink_alias_cannot_bypass_exclusion(tmp_path):
    root = tmp_path / "EBooks"
    profile = root / "A"
    (profile / "Acquisitions").mkdir(parents=True)
    (profile / "Outbox").mkdir()
    (profile / "alias").symlink_to(profile / "Outbox", target_is_directory=True)
    assert check(profile / "alias" / "book.epub", root)
    assert check(profile / "Acquisitions" / ".." / "Outbox" / "book.epub", root)
    assert check(Path("A/Outbox/book.epub"), root)
    assert check(root / "A" / "Outbox" / "book.epub",
                 root, identity="B", owner="B")


def test_plan_checks_source_keeper_and_intermediate_destinations(tmp_path):
    root = tmp_path / "EBooks"
    normal = root / "A" / "Acquisitions" / "book.epub"
    exclude = root / "A" / "Outbox" / "reader-1" / "book.epub"
    kwargs = dict(library_root=root, identity_id="A", owner_directory="A")
    assert not ordinary_dedup_paths_excluded((normal, normal), **kwargs)
    assert ordinary_dedup_paths_excluded((normal, exclude), **kwargs)
    assert ordinary_dedup_paths_excluded((exclude, normal), **kwargs)
    assert ordinary_dedup_paths_excluded((normal, exclude, normal), **kwargs)
    assert ordinary_dedup_paths_excluded((), **kwargs)


def test_actual_preview_and_postplacement_exclude_outbox_in_both_directions(
    store, tmp_path,
):
    root = tmp_path / "EBooks"
    first_source, a = new_source("A", provider="seller-a", credential="card-a")
    second_source, b = new_source("A", provider="seller-b", credential="card-b")
    register(store, "A", "seller-a", "card-a")
    register(store, "A", "seller-b", "card-b")
    first = publish(store, root, target(tmp_path, "A", "first.pdf"), first_source, a)
    second = publish(store, root, target(tmp_path, "A", "second.pdf"), second_source, b)
    outbox_file = root / "A" / "Outbox" / "reader-x" / "third.pdf"
    outbox_file.parent.mkdir(parents=True)
    outbox_file.write_bytes(BYTES)
    outbox = store.placement_put({
        "artifact_id": first.artifact_id, "path": str(outbox_file),
        "identity_id": "A", "provider_id": None,
        "source_item_id": None, "state": "present", "metadata": {},
    })

    def preview(candidate, keeper):
        return preview_canonical_duplicate(
            store, library_root=root, candidate_placement_id=candidate,
            keeper_placement_id=keeper,
        )

    assert preview(outbox, first.placement_id).reason == "excluded_path"
    assert preview(second.placement_id, outbox).reason == "excluded_path"
    assert not preview(outbox, first.placement_id).may_attempt_retirement
    assert not preview(second.placement_id, outbox).may_attempt_retirement
    ordinary = preview(second.placement_id, first.placement_id)
    assert ordinary.status == "unreferenced"
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) == (second.placement_id,)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=outbox,
    ) == ()
    assert outbox_file.read_bytes() == BYTES
    assert store.placement_get(outbox)["state"] == "present"


def test_many_outbox_registrations_do_not_consume_ordinary_duplicate_budget(
    store, tmp_path,
):
    from tests.test_core_p0_3719_post_placement_duplicates import _two_accounts

    root, paths, (first, second, foreign) = _two_accounts(store, tmp_path)
    with store._lock, store._connect() as db:
        db.executemany(
            "INSERT INTO placements(placement_id,artifact_id,path,identity_id,"
            "state,metadata_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            [
                (f"outbox-{n:03d}", first.artifact_id,
                 str(root / "A" / "Outbox" / "reader-1" / f"{n:03d}.pdf"),
                 "A", "present", "{}", 1, 1)
                for n in range(70)
            ],
        )
    # 70 Outbox delivery copies are not part of the ordinary 64 candidates.
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=first.placement_id,
    ) == (second.placement_id,)
    assert verified_post_placement_duplicates(
        store, library_root=root, placement_id=second.placement_id,
    ) == (first.placement_id,)
    assert all(path.read_bytes() == BYTES for path in paths)
