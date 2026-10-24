"""P0 #2797: corrupt local declaration namespace never weakens conflict checks."""
import pytest

from cifereca.core import library_declared_links as engine
from tests.test_library_declared_links import rig, call, rows


@pytest.mark.parametrize("raw, expected_code", [
    ('{"private_path":"PRIVATE_SECRET",', "invalid_local_context"),
    ('{"version":1,"version":1}', "declaration_store_invalid"),
    ('{"scope":{"account_id":"a","account_id":"b"}}', "declaration_store_invalid"),
    ('[1,2,3]', "declaration_store_invalid"),
    ('"PRIVATE_SECRET"', "declaration_store_invalid"),
    ('{"version": NaN}', "declaration_store_invalid"),
    ('{"version": Infinity}', "declaration_store_invalid"),
])
def test_corrupt_or_ambiguous_json_fails_closed_without_private_error(rig, raw, expected_code):
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
    assert str(caught.value) == expected_code
    # The public preview also fails with a closed code; no confirmation token.
    with pytest.raises(engine.DeclarationRejected) as public:
        call(rig)
    assert str(public.value) == expected_code
    assert rows(service, "entitlements") == rights
    assert rows(service, "placements") == placements
    assert book.read_bytes() == before
    assert "PRIVATE_SECRET" not in str(public.value)
