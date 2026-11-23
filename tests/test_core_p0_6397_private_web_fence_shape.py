"""#6397: malformed eviction fences cannot crash private web reads."""
from types import SimpleNamespace
import pytest
from cifereca.core.notice_c_authenticated_web_retention import retained_private_web_field_txn

DOC = SimpleNamespace(provider_id="numilog", source_key="notice-c:v1:source")
MEMBER = SimpleNamespace(visibility="private", provider_id="numilog", source_ref="notice-c:v1:source", source_epoch=1)

@pytest.mark.parametrize("fence", [None, "", 2, [], {}, {"evicted": True},
    {"evicted": True, "epoch": None}, {"evicted": True, "epoch": True},
    {"evicted": True, "epoch": "2"}, {"evicted": False, "epoch": 2}])
def test_malformed_fence_returns_none_without_database_access(fence):
    assert retained_private_web_field_txn(None, DOC, MEMBER, fence) is None
