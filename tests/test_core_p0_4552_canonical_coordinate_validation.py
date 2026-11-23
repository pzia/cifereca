"""#4552: canonical references reject ambiguous coordinates before digesting."""
import pytest
from cifereca.core.notice_c_cover_index import canonical_cover_document_ref

@pytest.mark.parametrize("edition", ["edition:../other", "edition:a..b", "https://example.org/edition", "edition:https://x"])
def test_ambiguous_canonical_edition_rejected(edition):
    with pytest.raises(ValueError, match="invalid_canonical_edition"):
        canonical_cover_document_ref(edition)

def test_valid_opaque_edition_still_accepted():
    assert canonical_cover_document_ref("edition:abcdef012345").source_key.startswith("notice-c:canonical-cover:v1:")
