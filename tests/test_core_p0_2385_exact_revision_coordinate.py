"""#2385: a revision coordinate must be as exact as provider/record/docbase."""
import pytest
from cifereca.core.notice_c_exact_source_lookup import _valid_coordinates

@pytest.mark.parametrize("revision", ["a..b", "https://revision", "a/b/../c"])
def test_ambiguous_revision_cannot_select_a_canonical_edition(revision):
    assert not _valid_coordinates(("paris", "record", "docbase", "digital", revision))

def test_ordinary_revision_is_admitted():
    assert _valid_coordinates(("paris", "record", "docbase", "digital", "rev:1"))
