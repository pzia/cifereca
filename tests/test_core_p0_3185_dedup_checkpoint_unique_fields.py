"""#3185: ambiguous maintenance checkpoints must not be accepted."""
import json

import pytest

from cifereca.core.canonical_post_placement import _unique_checkpoint_fields


@pytest.mark.parametrize("payload", [
    '{"schema":"canonical.post-placement-dedup-pending.v1","schema":"other"}',
    '{"placement_ids":["p1","p2"],"placement_ids":["p1"]}',
    '{"nested":{"identity_id":"A","identity_id":"B"}}',
])
def test_duplicate_checkpoint_json_keys_are_rejected(payload):
    with pytest.raises(ValueError, match="canonical_dedup_duplicate_checkpoint_field"):
        json.loads(payload, object_pairs_hook=_unique_checkpoint_fields)


def test_unique_checkpoint_json_keys_remain_compatible():
    payload = '{"schema":"canonical.post-placement-dedup-pending.v1","placement_ids":["a","b"]}'
    result = json.loads(payload, object_pairs_hook=_unique_checkpoint_fields)
    assert result["placement_ids"] == ["a", "b"]
