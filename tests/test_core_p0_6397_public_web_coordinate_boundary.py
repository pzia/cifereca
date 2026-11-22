"""#6397: public C references reject foreign source coordinates without callbacks."""
import pytest

from cifereca.core.notice_c_web_references import decode_public_web_references


class HostileCoordinate:
    def __eq__(self, other):
        raise AssertionError("foreign equality invoked")

    def __str__(self):
        raise AssertionError("foreign conversion invoked")


_BASE = dict(
    provider_id="numilog-catalog", source_ref="notice-c:v1:source",
    record_id="book-42", edition_id="edition-42", source_revision="rev-1",
    observed_at=1,
)


@pytest.mark.parametrize("field,value", [
    ("provider_id", HostileCoordinate()),
    ("source_ref", HostileCoordinate()),
    ("record_id", HostileCoordinate()),
    ("edition_id", HostileCoordinate()),
    ("source_revision", HostileCoordinate()),
    ("observed_at", True),
    ("observed_at", -1),
    ("record_id", ""),
    ("source_ref", "x" * 513),
])
def test_public_web_decoder_rejects_untrusted_source_coordinates(field, value):
    args = {**_BASE, field: value}
    with pytest.raises(ValueError, match="invalid_public_web_source_coordinates"):
        decode_public_web_references({
            "schema": 1, "links": [{
                "url": "https://www.numilog.com/livre/42",
                "evidence": "numilog-catalog.public_search_product_card",
            }],
        }, **args)
