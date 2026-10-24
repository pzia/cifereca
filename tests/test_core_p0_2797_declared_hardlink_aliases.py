"""P0 #2797: user declarations distinguish physical files from byte copies.

A hardlink is the SAME inode through another path, and cannot silently give
two borrowers separate claims to one physical local file. Identical EPUB
bytes stored as two independent inodes remain separate, user-declarable
statements (never purchase attestations, receipts or placements).
"""
from __future__ import annotations

import os

import pytest

from cifereca.core import library_declared_links as engine
from cifereca.core.library_inventory import scan_identity_roots
from tests.test_library_declared_links import rig, records, rows
from tests.test_core_p0_2797_paris_multi_borrower_declaration import paris_borrowers


def _observed(root):
    return {row.path: row for row in
            scan_identity_roots({"default": (root,)}).files}


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_two_paths_one_inode_cannot_declare_two_paris_borrowers(paris_borrowers):
    service, first_path, second_path, _, _, opts = paris_borrowers
    primary = opts("primary", first_path)
    first_preview = engine.declare_local_link(service, **primary)
    first = engine.declare_local_link(
        service, **primary, confirmation=first_preview.plan_token,
    )
    before = records(service)
    rights = rows(service, "entitlements")

    # Creating the link alters ctime but preserves the physical dev/inode.
    # This is the case that a path-only collision check wrongly accepted.
    second_path.unlink()
    os.link(first_path, second_path)
    files = _observed(first_path.parent)
    assert files[str(first_path)].sha256 == files[str(second_path)].sha256
    assert files[str(first_path)].fingerprint[:2] == files[str(second_path)].fingerprint[:2]
    assert files[str(first_path)].path != files[str(second_path)].path

    with pytest.raises(engine.DeclarationRejected, match="^declaration_conflict$"):
        engine.declare_local_link(
            service, **{
                **opts("paired-uid", second_path),
                "observation": files[str(second_path)],
            },
        )
    assert records(service) == before
    assert len(records(service)) == 1
    assert records(service)[0]["relation_id"] == first.relation_id
    assert rows(service, "entitlements") == rights
    assert not rows(service, "placements")
    assert not rows(service, "acquisition_ledger")


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_independent_same_sha_copies_remain_declared_not_attested(paris_borrowers):
    service, first_path, second_path, _, _, opts = paris_borrowers
    second_path.write_bytes(first_path.read_bytes())
    files = _observed(first_path.parent)
    first, second = files[str(first_path)], files[str(second_path)]
    assert first.sha256 == second.sha256
    assert first.fingerprint[:2] != second.fingerprint[:2]

    for borrower, path, observation in [
        ("primary", first_path, first), ("paired-uid", second_path, second),
    ]:
        options = {**opts(borrower, path), "observation": observation}
        preview = engine.declare_local_link(service, **options)
        assert preview.status == "preview"
        result = engine.declare_local_link(
            service, **options, confirmation=preview.plan_token,
        )
        assert result.status == "declared"
        assert result.trust == "user_declaration"
        assert result.rights_mutations == 0
        assert result.file_mutations == 0
        assert result.placement_mutations == 0
    assert len(records(service)) == 2
    assert {row["scope"]["account_id"] for row in records(service)} == {
        "primary", "paired-uid",
    }
    assert not rows(service, "placements")
    assert not rows(service, "acquisition_ledger")


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_renamed_hardlink_still_conflicts_until_user_revokes(paris_borrowers):
    service, first_path, second_path, _, _, opts = paris_borrowers
    primary = opts("primary", first_path)
    preview = engine.declare_local_link(service, **primary)
    created = engine.declare_local_link(
        service, **primary, confirmation=preview.plan_token,
    )
    # Previously declared path disappears. The underlying inode survives at
    # a new path, which is NOT a new account entitlement.
    second_path.unlink()
    first_path.rename(second_path)
    relocated = _observed(second_path.parent)[str(second_path)]
    with pytest.raises(engine.DeclarationRejected, match="^declaration_conflict$"):
        engine.declare_local_link(
            service, **{
                **opts("paired-uid", second_path),
                "observation": relocated,
            },
        )
    assert len(records(service)) == 1
    assert records(service)[0]["relation_id"] == created.relation_id
    assert not rows(service, "placements")
