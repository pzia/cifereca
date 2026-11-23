"""#6397: never silently trim source provenance of a public web action."""
import pytest
from cifereca.core.notice_c_web_references import decode_public_web_references

@pytest.mark.parametrize("name", ["provider_id", "source_ref", "record_id", "edition_id", "source_revision"])
@pytest.mark.parametrize("padding", [" ", "\t"])
def test_reject_padded_public_web_coordinate(name, padding):
    fields = dict(provider_id="paris", source_ref="notice-c:v1:" + "a"*64, record_id="record", edition_id="edition", source_revision="rev", observed_at=1)
    fields[name] = padding + fields[name]
    with pytest.raises(ValueError, match="invalid_public_web_source_coordinates"):
        decode_public_web_references({"schema": 1, "links": []}, **fields)
