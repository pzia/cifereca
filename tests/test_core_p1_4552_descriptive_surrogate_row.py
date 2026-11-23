"""#4552: corrupt SQLite Unicode must fail closed with the stable Core error."""
import pytest

from cifereca.core.notice_descriptive_materialization_store import _decode


@pytest.mark.parametrize("payload", [
    "\ud800",
    "\udfff",
    '{"value":"\ud800"}',
    '{"value":"\udfff"}',
])
def test_unpaired_surrogates_are_corrupt_rows(payload):
    with pytest.raises(ValueError, match="^corrupt_descriptive_materialization$"):
        _decode(payload)


def test_non_string_payload_is_corrupt():
    with pytest.raises(ValueError, match="^corrupt_descriptive_materialization$"):
        _decode(b"{}")
