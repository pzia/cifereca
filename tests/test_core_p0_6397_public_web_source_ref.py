"""#6397: a public web action must be tied to an exact opaque C source."""
import pytest
from cifereca.core.notice_c_web_references import decode_public_web_references

@pytest.mark.parametrize("ref", ["other:source", "notice-c:v1:short", "notice-c:v1:" + "G"*64, "notice-c:v1:" + "a"*63 + "/"])
def test_reject_non_source_web_reference(ref):
    with pytest.raises(ValueError, match="invalid_public_web_source_coordinates"):
        decode_public_web_references({"schema": 1, "links": []}, provider_id="paris", source_ref=ref, record_id="1", edition_id="edition:1", source_revision="rev", observed_at=1)
