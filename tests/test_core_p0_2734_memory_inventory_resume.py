"""#2734: memory-only partial inventory resume and fail-closed freshness.

Only synthetic EPUBs and in-memory private observations are used. Reusing a
file's SHA/CRC is not reusing an acquisition right or the SQLite/WAL snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from unittest.mock import patch

from cifereca.core.library_inventory import InventoryLimits, scan_identity_roots
from cifereca.core.library_inventory_checkpoint import (
    begin_inventory_resume, remember_inventory_resume,
)
from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import epub, filesystem


def _books(tmp_path, count=4):
    root = tmp_path / "PRIVATE_SCOPE"
    root.mkdir()
    for i in range(count):
        epub(root / f"PRIVATE_{i:03d}.epub", title=f"PRIVATE_TITLE_{i}")
    return root


def _scan(root, *, reuse=None, **kwargs):
    return scan_identity_roots(
        {"alice": (root,)}, _reuse=reuse, **kwargs,
    )


def _reuse(inventory):
    return {(row.identity_id, row.path): row for row in inventory.files}


def test_second_full_scan_reuses_sha_opf_crc_but_still_enumerates(tmp_path):
    root = _books(tmp_path)
    before = filesystem(tmp_path)
    first = _scan(root)
    assert not first.reasons and len(first.files) == 4
    assert first.phase_calls["sha256"] == 4
    with patch("cifereca.core.library_inventory.inspect_epub",
               side_effect=AssertionError("unexpected OPF read")):
        second = _scan(root, reuse=_reuse(first))
    assert second.reasons == set()
    assert second.entries == first.entries
    assert second.redacted()["status"] == "complete"
    assert second.reused_files == 4
    assert second.newly_observed_files == 0
    assert second.revalidated_files == second.stale_files == 0
    assert second.files_started == 4
    assert second.phase_calls["sha256"] == 0
    assert second.phase_calls["structure"] == 0
    assert second.hash_bytes == 0  # *current* run's actual byte reads
    assert {x.sha256 for x in second.files} == {x.sha256 for x in first.files}
    assert filesystem(tmp_path) == before
    public = json.dumps(second.redacted())
    for forbidden in ("PRIVATE_", str(root), first.files[0].sha256):
        assert forbidden not in public


def test_cancelled_partial_run_resumes_only_completed_files(tmp_path):
    root = _books(tmp_path)
    stop = [False]

    def progress(row):
        if row["files"] == 2:
            stop[0] = True

    first = _scan(root, cancelled=lambda: stop[0], progress=progress)
    assert first.reasons == {"cancelled"}
    assert len(first.files) == 2
    assert first.redacted()["status"] == "incomplete_scan"
    full = _scan(root, reuse=_reuse(first))
    assert len(full.files) == 4
    assert full.reused_files == 2
    assert full.newly_observed_files == 2
    assert full.phase_calls["sha256"] == 2
    assert full.reasons == set()
    assert full.redacted()["status"] == "complete"


def test_changed_bytes_and_atomic_replacement_invalidate_cached_file(tmp_path):
    root = _books(tmp_path, 2)
    initial = _scan(root)
    book = root / "PRIVATE_000.epub"
    old = book.stat()
    replacement = tmp_path / "replacement.epub"
    epub(replacement, title="PRIVATE_CHANGED")
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    os.replace(replacement, book)
    result = _scan(root, reuse=_reuse(initial))
    assert result.reused_files == 1
    assert result.revalidated_files == result.stale_files == 1
    assert result.phase_calls["sha256"] == 1
    first_digest = next(f.sha256 for f in initial.files if f.path == str(book))
    current_digest = next(f.sha256 for f in result.files if f.path == str(book))
    assert current_digest != first_digest
    assert result.reasons == set()


def test_renamed_path_is_new_not_a_negative_missing_file_assertion(tmp_path):
    root = _books(tmp_path, 1)
    first = _scan(root)
    (root / "PRIVATE_000.epub").rename(root / "PRIVATE_RENAMED.epub")
    again = _scan(root, reuse=_reuse(first))
    assert again.reused_files == 0
    assert again.newly_observed_files == 1
    assert len(again.files) == 1
    assert again.redacted()["status"] == "complete"
    assert "missing" not in json.dumps(again.redacted())


def test_symlink_replacement_never_reuses_file_evidence(tmp_path):
    root = _books(tmp_path, 1)
    before = _scan(root)
    book = root / "PRIVATE_000.epub"
    outside = tmp_path / "OUTSIDE.epub"
    epub(outside)
    book.unlink()
    book.symlink_to(outside)
    again = _scan(root, reuse=_reuse(before))
    assert again.files == []
    assert again.reused_files == 0
    assert again.skipped_symlinks == 1


def test_hardlink_now_present_forces_crc_and_hash_revalidation(tmp_path):
    root = _books(tmp_path, 1)
    first = _scan(root)
    (tmp_path / "HARDLINK.epub").hardlink_to(root / "PRIVATE_000.epub")
    second = _scan(root, reuse=_reuse(first))
    assert second.reused_files == 0
    assert second.revalidated_files == 1
    assert second.phase_calls["sha256"] == 1
    assert second.phase_calls["structure"] == 1


def test_changed_identity_never_matches_previous_observations(tmp_path):
    root = _books(tmp_path, 1)
    alice = _scan(root)
    bob = scan_identity_roots({"bob": (root,)}, _reuse=_reuse(alice))
    assert bob.reused_files == 0
    assert bob.newly_observed_files == 1
    assert bob.files[0].identity_id == "bob"


def test_modified_metadata_budget_cannot_reuse_without_checking_limit(tmp_path):
    root = _books(tmp_path, 1)
    initial = _scan(root)
    # The cached OPF would exceed the new metadata budget. Reparse under
    # the current stricter budget instead of importing unbounded old data.
    limited = _scan(
        root, reuse=_reuse(initial),
        limits=InventoryLimits(metadata_bytes=1),
    )
    assert limited.reused_files == 0
    assert limited.phase_calls["sha256"] == 1
    assert limited.files[0].metadata == {}
    assert "metadata_byte_budget" in limited.reasons


def test_checkpoint_scopes_generation_and_never_serializes_private_rows(tmp_path):
    root = _books(tmp_path, 1)
    inventory = _scan(root)
    service = object()
    scope = ("current", 42, (str(root),))
    old_generation, empty = begin_inventory_resume(service, "alice", scope)
    assert not empty
    remember_inventory_resume(service, "alice", scope, old_generation,
                              inventory, lambda: True)
    generation, cached = begin_inventory_resume(service, "alice", scope)
    assert len(cached) == 1
    assert next(iter(cached.values())).sha256 == inventory.files[0].sha256
    # An old in-flight audit can never replace the newer generation.
    remember_inventory_resume(service, "alice", scope, old_generation,
                              inventory, lambda: True)
    remember_inventory_resume(service, "alice", scope, generation,
                              inventory, lambda: True)
    _, rejected = begin_inventory_resume(service, "bob", scope)
    assert rejected == {}
    # The checkpoint is not a public data type or a persistent file.
    assert not list(tmp_path.rglob("*.checkpoint"))


def test_broker_reopens_fresh_sqlite_and_reuses_only_file_data(scoped, tmp_path):
    service, database, root = scoped
    broker = LibraryReconciliationReadBrokerImpl(service)
    before = filesystem(tmp_path)
    first = broker.audit(identity_id="alice")
    assert first["inventory"]["reused_files"] == 0
    second = broker.audit(identity_id="alice")
    assert second["inventory"]["reused_files"] == 1
    assert second["inventory"]["phase_calls"]["sha256"] == 0
    assert second["status"] == "complete"
    assert filesystem(tmp_path) == before
    # A later DB commit must be observed from a NEW snapshot, despite
    # perfectly unchanged EPUB bytes cached from the first audit.
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements (entitlement_id,identity_id,state,provider_id)"
            " VALUES (?,?,?,?)",
            ("PRIVATE-new-right", "alice", "active", "numilog"),
        )
    third = broker.audit(identity_id="alice")
    assert third["inventory"]["reused_files"] == 1
    assert third["reconciliation"]["counts"]["rights_without_verified_file"] == 1
    public = json.dumps(third)
    assert "PRIVATE-" not in public
    assert str(root) not in public


def test_broker_change_epoch_disables_checkpoint_reuse(scoped):
    service, _, _ = scoped
    broker = LibraryReconciliationReadBrokerImpl(service)
    first = broker.audit(identity_id="alice")
    assert first["status"] == "complete"
    service._account_context_epoch += 1
    second = broker.audit(identity_id="alice")
    assert second["status"] == "complete"
    assert second["inventory"]["reused_files"] == 0


def test_broker_can_resume_after_file_inventory_cancellation(scoped):
    from types import SimpleNamespace
    service, _, _ = scoped
    broker = LibraryReconciliationReadBrokerImpl(service)
    stop = [False]

    def progress(**event):
        if (event.get("message") == "Inventaire local"
                and event.get("current") == 1):
            stop[0] = True

    cancelled = broker.audit(
        identity_id="alice",
        control=SimpleNamespace(cancelled=lambda: stop[0], report=progress),
    )
    assert cancelled["status"] == "incomplete_scan"
    assert cancelled["reasons"] == ["audit_cancelled"]
    recovered = broker.audit(identity_id="alice")
    assert recovered["status"] == "complete"
    assert recovered["inventory"]["reused_files"] == 1


def test_interleaved_identities_keep_independent_verified_checkpoints(tmp_path):
    """An audit of B must not evict an eligible memory-only A checkpoint."""
    alice_root = tmp_path / "ALICE_PRIVATE"
    bob_root = tmp_path / "BOB_PRIVATE"
    alice_root.mkdir()
    bob_root.mkdir()
    epub(alice_root / "alice.epub")
    epub(bob_root / "bob.epub")
    service = object()
    alice = scan_identity_roots({"alice": (alice_root,)})
    bob = scan_identity_roots({"bob": (bob_root,)})
    scope_a = ("config", "alice", str(alice_root))
    scope_b = ("config", "bob", str(bob_root))

    ga, unused_a = begin_inventory_resume(service, "alice", scope_a)
    assert not unused_a
    remember_inventory_resume(service, "alice", scope_a, ga, alice, lambda: True)
    gb, unused_b = begin_inventory_resume(service, "bob", scope_b)
    assert not unused_b
    remember_inventory_resume(service, "bob", scope_b, gb, bob, lambda: True)
    next_a, cached_a = begin_inventory_resume(service, "alice", scope_a)
    assert len(cached_a) == 1
    with patch("cifereca.core.library_inventory.inspect_epub",
               side_effect=AssertionError("A was unexpectedly rescanned")):
        reused = scan_identity_roots({"alice": (alice_root,)}, _reuse=cached_a)
    assert reused.reused_files == 1 and reused.phase_calls["structure"] == 0
    next_b, cached_b = begin_inventory_resume(service, "bob", scope_b)
    assert len(cached_b) == 1
    assert {row.identity_id for row in cached_b.values()} == {"bob"}
    assert set(cached_a).isdisjoint(set(cached_b))
    # Checkpoints still never contain SQLite rights or share observations.
    assert all(row.identity_id == "alice" for row in cached_a.values())
    assert next_a != next_b


def test_interleaved_generations_only_supersede_same_profile(tmp_path):
    root = _books(tmp_path, 1)
    inventory = _scan(root)
    service = object()
    scope_a = ("alice", str(root))
    scope_b = ("bob", str(root))
    ga, _ = begin_inventory_resume(service, "alice", scope_a)
    gb, _ = begin_inventory_resume(service, "bob", scope_b)
    # The unrelated bob audit must not invalidate the pending alice run.
    remember_inventory_resume(service, "alice", scope_a, ga, inventory, lambda: True)
    _next, cached = begin_inventory_resume(service, "alice", scope_a)
    assert len(cached) == 1
    # The previous alice generation, however, cannot resurrect its slot.
    remember_inventory_resume(service, "alice", scope_a, ga, inventory, lambda: True)
    # Neither can an inventory of alice be accidentally stored under bob.
    remember_inventory_resume(service, "bob", scope_b, gb, inventory, lambda: True)
    _, wrong = begin_inventory_resume(service, "bob", scope_b)
    assert wrong == {}


def test_multi_profile_cache_is_bounded_by_slot_count(tmp_path):
    from cifereca.core import library_inventory_checkpoint as cache
    root = _books(tmp_path, 1)
    inventory = _scan(root)
    services = [object() for _ in range(cache._MAX_SLOTS + 1)]
    for index, service in enumerate(services):
        scope = ("different-service", index)
        generation, _ = begin_inventory_resume(service, "alice", scope)
        remember_inventory_resume(service, "alice", scope, generation,
                                  inventory, lambda: True)
    assert len(cache._SLOTS) <= cache._MAX_SLOTS
    assert sum(len(slot.rows) for slot in cache._SLOTS) <= cache._MAX_FILES
    _, old = begin_inventory_resume(services[0], "alice",
                                   ("different-service", 0))
    assert not old
    _, newest = begin_inventory_resume(
        services[-1], "alice", ("different-service", len(services)-1))
    assert len(newest) == 1
