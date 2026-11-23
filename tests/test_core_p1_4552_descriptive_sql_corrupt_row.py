"""#4552: malformed SQLite metadata is rejected before any source callbacks."""
import json

import pytest

from cifereca.core.notice_descriptive_materialization_store import _decode


@pytest.mark.parametrize("patch", [
    {"version": True},
    {"inherited": 0},
    {"execution_key": 42},
    {"canonical_type": []},
    {"scope": None},
    {"source_evidence_keys": [["bnf", "edition"]]},
    {"source_evidence_keys": [["bnf", "edition", 5]]},
    {"source_evidence_keys": [["a", "b", "c"]] * 129},
])
def test_malformed_persisted_envelope_fails_closed(patch):
    payload = dict(
        version=1, execution_key="0" * 64, canonical_type="age",
        target_edition_id="isbn:9780306406157", scope="fr:editions",
        plugin_id="plugin.age.alpha", plugin_version="1.0.0",
        schema_id="schema.age.v1", value="13+", inherited=False,
        source_evidence_keys=[["bnf", "isbn:9780306406157", "proof1"]],
    )
    payload.update(patch)
    with pytest.raises(ValueError, match="corrupt_descriptive_materialization"):
        _decode(json.dumps(payload))


def test_duplicate_json_keys_fail_closed():
    with pytest.raises(ValueError, match="corrupt_descriptive_materialization"):
        _decode('{"version":1,"version":2}')
