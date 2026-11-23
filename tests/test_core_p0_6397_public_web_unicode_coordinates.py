"""#6397: invisible source coordinates cannot label admitted public web references."""
import pytest

from cifereca.core.notice_c_web_references import decode_public_web_references


@pytest.mark.parametrize("field", [
    "provider_id", "source_ref", "record_id", "edition_id", "source_revision",
])
@pytest.mark.parametrize("control", [chr(0x200b), chr(0x202e), chr(0x2028), chr(0x2029), chr(0x0080)])
def test_public_web_rejects_invisible_source_coordinate(field, control):
    coordinates = dict(provider_id="numilog-catalog", source_ref="notice-c:v1:source",
                       record_id="book-42", edition_id="edition-42",
                       source_revision="rev-1", observed_at=1)
    coordinates[field] += control
    with pytest.raises(ValueError, match="invalid_public_web_source_coordinates"):
        decode_public_web_references({"schema": 1, "links": [
            {"url": "https://www.numilog.com/livre/42",
             "evidence": "numilog-catalog.public_search_product_card"},
        ]}, **coordinates)
