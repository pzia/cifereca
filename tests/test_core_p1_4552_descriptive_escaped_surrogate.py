"""#4552: escaped Unicode surrogates cannot enter decoded trusted rows."""
import json

import pytest

from cifereca.core.notice_descriptive_materialization_store import _decode


def valid_payload():
    return {
        "version": 1, "execution_key": "0" * 64,
        "canonical_type": "age", "target_edition_id": "edition:1",
        "scope": "fr:editions", "plugin_id": "plugin.age.alpha",
        "plugin_version": "1.0.0", "schema_id": "schema.age",
        "value": "13+", "inherited": False,
        "source_evidence_keys": [["source", "edition:1", "evidence"]],
    }


@pytest.mark.parametrize("field", [
    "execution_key", "canonical_type", "target_edition_id", "scope",
    "plugin_id", "plugin_version", "schema_id", "value",
])
def test_escaped_surrogate_in_scalar_rejected(field):
    payload = valid_payload()
    payload[field] = "\ud800"
    with pytest.raises(ValueError, match="^corrupt_descriptive_materialization$"):
        _decode(json.dumps(payload))


@pytest.mark.parametrize("index", [0, 1, 2])
def test_escaped_surrogate_in_provenance_rejected(index):
    payload = valid_payload()
    payload["source_evidence_keys"][0][index] = "\udfff"
    with pytest.raises(ValueError, match="^corrupt_descriptive_materialization$"):
        _decode(json.dumps(payload))
