"""#3719: disabled source must never publish B after a completed file GET.

Synthetic SQLite v14 and PDF bytes. No provider I/O, ADEPT, or UI.
The source preference is distinct from the existing right/auto consent:
both must still be valid at final publication.
"""
from __future__ import annotations

import pytest

from cifereca.core import canonical_publication
from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.source_auto_consent_bridge import _source_id
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import (
    SHA, new_source, register, rows, target,
)


@pytest.fixture
def publication(tmp_path):
    db = Storage(tmp_path / "state.sqlite")
    try:
        assert db.schema_version() == 14
        yield db, tmp_path / "EBooks", tmp_path
    finally:
        db.close()


def _publish(db, root, path, source, snapshot):
    return publish_completed_acquisition(
        db, library_root=root, path=path, source=source,
        snapshot=snapshot, final_sha256=SHA,
        delivery_receipt_ref="synthetic-completed-get",
    )


@pytest.mark.parametrize(("provider", "kind"), [
    ("seller", "purchase"), ("kobo", "purchase"), ("bnf-pret", "loan"),
])
@pytest.mark.parametrize("disabled", ["0", "false", "OFF", "invalid"])
def test_disabled_or_malformed_source_denies_final_b_sqlite_publication(
    publication, provider, kind, disabled,
):
    db, root, tmp_path = publication
    source, snapshot = new_source(provider=provider, kind=kind)
    register(db, "A", provider, "card-A")
    path = target(tmp_path, "A")
    sid = _source_id(source.scope)
    pref = "sync.source.enabled." + sid
    # Even a previously authorized / completed download cannot be published
    # after its independent source switch is revoked.
    db.metadata_set(pref, disabled)
    with pytest.raises(ValueError, match="canonical_publisher_source_disabled"):
        _publish(db, root, path, source, snapshot)
    assert rows(db) == (0, 0, 0, 0)
    assert path.is_file()

    # The same untouched bytes may be admitted by a NEW, authorized attempt.
    db.metadata_set(pref, "1")
    assert _publish(db, root, path, source, snapshot).changed is True
    assert _publish(db, root, path, source, snapshot).changed is False
    assert rows(db) == (1, 1, 1, 1)


def test_disable_after_sha_before_begin_immediate_blocks_old_publication(
    publication, monkeypatch,
):
    db, root, tmp_path = publication
    source, snapshot = new_source(provider="kobo")
    register(db, "A", "kobo", "card-A")
    path = target(tmp_path, "A")
    pref = "sync.source.enabled." + _source_id(source.scope)
    db.metadata_set(pref, "1")
    original_sha256 = canonical_publication.LocalCatalog.sha256
    revoked = []

    def revoke_after_preflight_hash(book):
        digest = original_sha256(book)
        if not revoked:
            revoked.append(True)
            # Another task disables the exact source after the last preflight
            # hash but BEFORE the publisher acquires its BEGIN IMMEDIATE lock.
            db.metadata_set(pref, "0")
        return digest

    monkeypatch.setattr(
        canonical_publication.LocalCatalog, "sha256",
        staticmethod(revoke_after_preflight_hash),
    )
    with pytest.raises(ValueError, match="canonical_publisher_source_disabled"):
        _publish(db, root, path, source, snapshot)
    assert revoked == [True]
    assert rows(db) == (0, 0, 0, 0)
    assert path.is_file()
