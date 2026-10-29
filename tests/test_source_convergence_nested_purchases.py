"""Six purchases: exact source first, final SHA after admitted delivery.

One Core engine, two interchangeable synthetic providers. Real SQLite/EPUB
files; no supplier network, ADEPT, file moves, deletions or inferred rights.
"""
from dataclasses import replace
import hashlib

import pytest

from cifereca.core.canonical_publication import publish_completed_acquisition
from cifereca.core.convergence_execution import CompletedDelivery
from cifereca.models import Account
from cifereca.storage import Storage
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, register, rows
from tests.test_core_p0_3171_convergence_execution import FakeService, run
from tests.test_library_reconciliation import epub


@pytest.fixture(params=["numilog", "seller-b"])
def purchases(tmp_path, monkeypatch, request):
    store = Storage(tmp_path / "store.sqlite")
    provider = request.param
    account = Account(identity_id="A", provider_id=provider,
                      credential_id="card-A", unique_id="buyer-A")
    register(store, "A", provider, "card-A", account="buyer-A")
    service = FakeService(store, [account])
    root = tmp_path / "EBooks"
    keys, rights, paths = [], [], []
    for i, directory in enumerate(("numilog", "numilog/acsm-one", "numilog/acsm-two/deep",
                                   "manual", "other/nested", "acsm-import")):
        source, snapshot = new_source("A", provider=provider,
                                      credential="card-A", account="buyer-A",
                                      edition=f"edition-{i}", right=f"purchase-{i}")
        keys.append(source)
        rights.append(replace(snapshot.rights[0], delivery="available",
                              delivery_offer_id=f"offer-{i}"))
        path = root / "A" / directory / f"book-{i}.epub"
        path.parent.mkdir(parents=True, exist_ok=True)
        epub(path, title=f"Distinct purchase {i}")
        paths.append(path)
    snapshot = replace(snapshot, rights=tuple(rights))
    lock_path = tmp_path / "state" / "acquisition.lock"
    for module in ("convergence_execution", "canonical_recovery"):
        monkeypatch.setattr("cifereca.core." + module + ".acquisition_lock_file",
                            lambda: lock_path)
    try:
        yield service, account, keys, snapshot, paths, root, lock_path
    finally:
        store.close()


def attest(store, root, path, key, snapshot):
    return publish_completed_acquisition(
        store, library_root=root, path=path, source=key, snapshot=snapshot,
        final_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        delivery_receipt_ref="synthetic-completed-" + key.right_id,
    )


def test_six_exact_nested_purchases_skip_all_gets_twice(purchases):
    service, account, keys, snapshot, paths, root, lock_path = purchases
    for key, path in zip(keys, paths):
        attest(service.storage, root, path, key, snapshot)
    placements = service.storage.placements()
    before = {p: p.read_bytes() for p in paths}
    for _ in range(2):
        result = run(service, account, snapshot, root, lock_path, (),
                     lambda *_: pytest.fail("exact local origins need no GET"))
        assert [x.status for x in result] == ["present_attested"] * 6
        assert rows(service.storage) == (6, 6, 6, 6)
        assert service.storage.placements() == placements
        assert {p: p.read_bytes() for p in paths} == before


def test_three_unproven_files_do_not_veto_authorized_get_and_sha_dedup(purchases):
    service, account, keys, snapshot, paths, root, lock_path = purchases
    from cifereca.catalog import LocalCatalog
    for i, (key, path) in enumerate(zip(keys, paths)):
        if i < 3:
            attest(service.storage, root, path, key, snapshot)
        # A local scan observes bytes without creating supplier provenance.
        indexed = LocalCatalog(service.storage, root).index_path(path, identity_id="A")
        assert indexed["status"] == "indexed"
    before = {p: p.read_bytes() for p in paths}
    original_placements = {x["placement_id"]: x for x in service.storage.placements()}
    denied = run(service, account, snapshot, root, lock_path, (),
                 lambda *_: pytest.fail("no GET without exact consent"))
    assert [x.status for x in denied] == ["present_attested"] * 3 + ["pending"] * 3
    assert rows(service.storage) == (6, 6, 3, 3)
    acquired = []

    def deliver(key, offer):
        index = keys.index(key)
        assert index >= 3
        acquired.append(key)
        path = root / "A" / "delivery" / (key.edition_id + ".epub")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(paths[index].read_bytes())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return CompletedDelivery(key, offer.delivery_offer_id, path, digest,
                                 "synthetic-new-delivery-" + key.right_id)

    result = run(service, account, snapshot, root, lock_path, set(keys[3:]), deliver)
    assert [x.status for x in result] == ["present_attested"] * 3 + ["downloaded"] * 3
    assert acquired == keys[3:]
    # Six distinct final binaries, nine preserved copies, six exact origins.
    assert rows(service.storage) == (6, 9, 6, 6)
    assert {p: p.read_bytes() for p in paths} == before
    for placement in service.storage.placements():
        if placement["placement_id"] in original_placements:
            assert placement == original_placements[placement["placement_id"]]
    again = run(service, account, snapshot, root, lock_path, set(keys[3:]),
                lambda *_: pytest.fail("second Sync must not repeat GET"))
    assert [x.status for x in again] == ["present_attested"] * 6
    assert rows(service.storage) == (6, 9, 6, 6)
