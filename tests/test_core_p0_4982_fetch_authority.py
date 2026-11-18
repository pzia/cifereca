"""#4982 ephemeral fetch authority: no provider transport or network."""
from contextlib import contextmanager
from dataclasses import replace

import pytest

from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_fetch_authority import PublicFetchAuthority
from cifereca.core.notice_cover_repository import read_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_4982_raw_input_receipt import digest, public

TRANSPORT = digest("declared-exact-transport")
IMPLEMENTATION = digest("declared-parser")


class Authority:
    def __init__(self):
        self.allowed = True

    @contextmanager
    def hold(self):
        yield self.allowed


def request(registry, source, authority, generation=0, ttl=30):
    return registry.request(
        source, transport_digest=TRANSPORT,
        implementation_digest=IMPLEMENTATION, hold_authority=authority.hold,
        ttl_seconds=ttl, expected_generation=generation,
    )


def admit(registry, lease, **options):
    return registry.dispatch_admission(lease, **{
        "transport_digest": TRANSPORT,
        "implementation_digest": IMPLEMENTATION, **options,
    })


def test_one_admission_and_atomic_completion_without_serialized_authority(tmp_path):
    db = Storage(tmp_path / "fetch.sqlite")
    registry = PublicFetchAuthority(db, clock=lambda: 100)
    owner = Authority()
    try:
        source = public(db)
        ref = request(registry, source, owner)
        job = journal.read_job(db, ref)
        assert job.spec.kind == "fetch_source_notice"
        assert job.state == "pending" and job.attempts == 0
        lease = journal.claim_job(db, ref, now=100, lease_seconds=30)
        with pytest.raises(ValueError, match="transport_changed"):
            with admit(registry, lease, transport_digest=digest("other")):
                pytest.fail("foreign transport admitted")
        accepted = []
        with admit(registry, lease) as permit:
            accepted.append(permit)
        with pytest.raises(ValueError, match="already_consumed"):
            with admit(registry, lease):
                pytest.fail("duplicate dispatch admitted")
        assert permit.source == source and permit.admitted_at == 100
        with pytest.raises(ValueError, match="invalid_permit"):
            with registry.publication_guard(replace(permit)):
                pytest.fail("copied permit admitted")
        with registry.publication_guard(permit) as verify:
            with db._lock, db._connect() as sql:
                sql.execute("BEGIN IMMEDIATE")
                assert verify(sql) == source
                with pytest.raises(ValueError, match="invalid_publication_guard"):
                    verify(sql)
                # Synthetic completion only: no supplier facts were changed.
                journal._complete_job_txn(sql, lease, now=100)
        with pytest.raises(ValueError, match="invalid_publication_guard"):
            verify(None)
        with pytest.raises(ValueError, match="authority_unavailable"):
            with registry.publication_guard(permit):
                pytest.fail("completed permit admitted")
        assert journal.read_job(db, ref).state == "current"
        assert read_notice_cover(db, source.document) == source
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        registry.close()
        db.close()


@pytest.mark.parametrize("mode", ("revoked", "expired", "source_changed", "superseded"))
def test_refusal_before_transport_admission(tmp_path, mode):
    db = Storage(tmp_path / "refused.sqlite")
    now = [100]
    registry = PublicFetchAuthority(db, clock=lambda: now[0])
    owner = Authority()
    try:
        source = public(db)
        ref = request(registry, source, owner)
        lease = journal.claim_job(db, ref, now=100, lease_seconds=60)
        if mode == "revoked":
            owner.allowed = False
        elif mode == "expired":
            now[0] = 130
        elif mode == "source_changed":
            public(db, at=11)
        else:
            new = request(registry, source, owner, generation=ref.generation)
            assert new.key == ref.key and new.generation == ref.generation + 1
        with pytest.raises(ValueError):
            with admit(registry, lease):
                pytest.fail("stale fetch authority")
    finally:
        registry.close()
        db.close()


def test_revoked_after_dispatch_and_restart_do_not_restore_permission(tmp_path):
    path = tmp_path / "restart.sqlite"
    db = Storage(path)
    owner = Authority()
    registry = PublicFetchAuthority(db, clock=lambda: 100)
    try:
        source = public(db)
        ref = request(registry, source, owner)
        lease = journal.claim_job(db, ref, now=100, lease_seconds=30)
        with admit(registry, lease) as permit:
            pass
        owner.allowed = False
        with pytest.raises(ValueError, match="authority_revoked"):
            with registry.publication_guard(permit):
                pytest.fail("revoked publication admitted")
        assert read_notice_cover(db, source.document) == source
        assert journal.read_job(db, ref).state == "pending"
    finally:
        registry.close()
        db.close()
    db = Storage(path)
    registry = PublicFetchAuthority(db, clock=lambda: 130)
    try:
        reclaimed = journal.claim_job(db, ref, now=130, lease_seconds=30)
        assert reclaimed is not None and reclaimed.fence > lease.fence
        with pytest.raises(ValueError, match="authority_unavailable"):
            with admit(registry, reclaimed):
                pytest.fail("durable row resurrected transport authority")
        assert read_notice_cover(db, source.document) == source
    finally:
        registry.close()
        db.close()


def test_refuse_request_without_current_authority(tmp_path):
    db = Storage(tmp_path / "denied.sqlite")
    registry = PublicFetchAuthority(db, clock=lambda: 100)
    owner = Authority()
    owner.allowed = False
    try:
        source = public(db)
        with pytest.raises(ValueError, match="authority_revoked"):
            request(registry, source, owner)
        with pytest.raises(ValueError, match="invalid_authority"):
            request(registry, source, owner, ttl=61)
        with db._lock, db._connect() as sql:
            assert sql.execute(
                "SELECT count(*) FROM metadata WHERE key LIKE ?",
                (journal._PREFIX + "%",),
            ).fetchone()[0] == 0
    finally:
        registry.close()
        db.close()
