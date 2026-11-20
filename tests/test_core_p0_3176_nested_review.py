"""#3176: read-only organizer must not own caller's SQLite transaction."""
import sqlite3

import pytest

from cifereca.core import library_organizer_review as review
from cifereca.core.canonical_placement_inventory import read_canonical_placement_inventory
from tests.test_core_p0_3171_atomic_publication_v14 import store, register
from tests.test_core_p0_3176_organizer_review import _service, _put_epub


@pytest.mark.parametrize("operation", ["inventory", "organizer", "binding", "facts", "destination"])
@pytest.mark.parametrize("settle", ["commit", "rollback"])
def test_nested_review_preserves_caller_transaction(store, tmp_path, operation, settle):
    root = tmp_path / "library"
    register(store, "A", "seller", "card-A")
    path, pid = _put_epub(store, root, "Acquisitions/current.epub")
    service = _service(store, root)
    scope = review.admit_organizer_review(service)
    assert scope is not None
    group, = read_canonical_placement_inventory(store, identity_id="A").groups
    db = store._connect()
    observer = sqlite3.connect(store.path)
    key = "synthetic.organizer.caller"
    before = path.read_bytes()
    try:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, "pending"))
        if operation == "inventory":
            assert read_canonical_placement_inventory(store, identity_id="A").status == "unavailable"
        elif operation == "organizer":
            assert review.run_organizer_review(service, scope) is None
        elif operation == "binding":
            assert scope.bound() is False
        elif operation == "facts":
            assert review._placement_facts(scope, group) is None
        else:
            assert review._destination_occupied(scope, path.with_name("free.epub")) is True
        assert db.in_transaction
        assert db.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() == ("pending",)
        assert observer.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() is None
        if settle == "commit":
            db.commit()
        else:
            db.rollback()
        assert observer.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone() == (
            ("pending",) if settle == "commit" else None)
        assert read_canonical_placement_inventory(store, identity_id="A").status == "reviewed"
        assert review.run_organizer_review(service, scope) is not None
        assert path.read_bytes() == before
        assert not path.with_name("free.epub").exists()
    finally:
        if db.in_transaction:
            db.rollback()
        observer.close()
