"""P0 #2797: corrupt local declaration namespace never weakens conflict checks."""
import pytest

from cifereca.core import library_declared_links as engine
from tests.test_library_declared_links import rig, call, rows


@pytest.mark.parametrize("raw", [
    '{"private_path":"PRIVATE_SECRET",',
    '{"version":1,"version":1}',
    '{"scope":{"account_id":"a","account_id":"b"}}',
    '[1,2,3]',
    '"PRIVATE_SECRET"',
    '{"version": NaN}',
])
def test_corrupt_or_ambiguous_json_fails_closed_without_private_error(rig, raw):
    service, book, _, _, _ = rig
    key = engine._PREFIX + "a" * 32
    before = book.read_bytes()
    with service.storage._lock, service.storage._connect() as db:
        db.execute("INSERT INTO metadata(key,value) VALUES(?,?)", (key, raw))
    rights = rows(service, "entitlements")
    placements = rows(service, "placements")
    with pytest.raises(engine.DeclarationRejected) as caught:
        with engine._transaction(service.storage, False) as db:
            engine._records(db)
    assert str(caught.value) == "declaration_store_invalid"
    # The public preview also fails with a closed code; no confirmation token.
    with pytest.raises(engine.DeclarationRejected) as public:
        call(rig)
    assert str(public.value) == "declaration_store_invalid"
    assert rows(service, "entitlements") == rights
    assert rows(service, "placements") == placements
    assert book.read_bytes() == before
    assert "PRIVATE_SECRET" not in str(public.value)
