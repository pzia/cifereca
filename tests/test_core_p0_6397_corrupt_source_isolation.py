"""#6397: one malformed public source must not hide other attested C links."""
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

import pytest

from cifereca.core import notice_c_web_choices as web


@pytest.mark.parametrize("error", [KeyError("cover_inventory_complete"),
                                    TypeError("bad shape"), AttributeError("bad source")])
def test_bad_source_fence_is_local_to_its_contribution(error):
    db = NS(execute=lambda *_args, **_kw: NS(fetchone=lambda: ("canonical",)))
    sources = (NS(source_ref="bad"), NS(source_ref="good"))
    snapshot = NS(epoch=3, sources=sources)
    good_ref = NS(provider_id="good", record_id="book", edition_id="edition",
                  observed_at=2, source_ref="good", source_revision="v1",
                  url="https://example.org/book/1")

    def read(_db, source):
        if source is sources[0]:
            raise error
        return (good_ref,)

    with patch.object(web, "_db_key", return_value="c-key"), \
         patch.object(web, "_decode", return_value=snapshot), \
         patch.object(web, "_current_refs", side_effect=read):
        result, pairs = web._read(db, NS(source_key="canonical"))
    assert result is snapshot
    assert len(pairs) == 1
    assert pairs[0][0].provider_id == "good"
    assert pairs[0][1] is good_ref


def test_storage_error_is_not_mistaken_for_one_corrupt_source():
    from sqlite3 import OperationalError
    db = NS(execute=lambda *_args, **_kw: NS(fetchone=lambda: ("canonical",)))
    snapshot = NS(epoch=3, sources=(NS(source_ref="bad"),))
    with patch.object(web, "_db_key", return_value="c-key"), \
         patch.object(web, "_decode", return_value=snapshot), \
         patch.object(web, "_current_refs", side_effect=OperationalError("offline")):
        with pytest.raises(OperationalError):
            web._read(db, NS(source_key="canonical"))
