"""#6590 JSON parsing safety; no provider or network needed."""
import pytest

from cifereca.plugin_api.catalogue_json import (
    CatalogueJSONError, parse_bounded_catalogue_json, validate_catalogue_json,
    MAX_JSON_BYTES, MAX_JSON_DEPTH, MAX_JSON_NODES, MAX_JSON_TEXT,
)


def test_wire_unicode_and_escaped_nesting():
    body = b'{"metadata":{"title":"Edition \\"[{}]\\u00e9"},"groups":[]}'
    result = parse_bounded_catalogue_json(body)
    assert result["metadata"]["title"] == 'Edition "[{}]é'
    assert validate_catalogue_json(result) is result


@pytest.mark.parametrize("body,code", (
    (b'{"metadata":{"title":"a","title":"b"}}', "duplicate_member"),
    (b'{"n":NaN}', "invalid_number"),
    (b'{"n":1e999}', "invalid_number"),
    (b'{"n":' + b'9'*129 + b'}', "budget"),
    (b'{"s":"\\ud800"}', "invalid_text"),
    (b'{"s":"\xff"}', "invalid_text"),
    (b'{"PRIVATE_TOKEN":', "malformed"),
    (b'['*(MAX_JSON_DEPTH+1) + b']'*(MAX_JSON_DEPTH+1), "budget"),
    (b' '* (MAX_JSON_BYTES+1), "budget"),
))
def test_closed_wire_errors(body, code):
    with pytest.raises(CatalogueJSONError) as error:
        parse_bounded_catalogue_json(body)
    assert error.value.code == code
    assert str(error.value) == code
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_already_decoded_untrusted_graphs():
    cycle = []
    cycle.append(cycle)
    for value in (cycle, [None]*MAX_JSON_NODES, {"x":"é"*MAX_JSON_TEXT}, 1<<500):
        with pytest.raises(CatalogueJSONError, match="budget"):
            validate_catalogue_json(value)
    for value in ({1:"x"}, (1,2), object()):
        with pytest.raises(CatalogueJSONError, match="invalid_structure"):
            validate_catalogue_json(value)
    with pytest.raises(CatalogueJSONError, match="invalid_number"):
        validate_catalogue_json(float("inf"))


def test_global_budget_counts_shared_subtrees():
    value = [None]*500
    with pytest.raises(CatalogueJSONError, match="budget"):
        validate_catalogue_json([value]*200)
