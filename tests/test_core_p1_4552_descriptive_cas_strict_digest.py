"""#4552: malformed CAS expectations never reach SQLite transactions."""
import pytest

from cifereca.storage import Storage
from cifereca.core.notice_descriptive_materialization import DescriptiveMaterialization
from cifereca.core.notice_descriptive_materialization_store import publish_descriptive_materialization


@pytest.mark.parametrize("expected", [
    "", "0" * 63, "G" * 64, "A" * 64, "0" * 63 + "z", 42, True, object(),
])
def test_noncanonical_descriptive_cas_rejected_before_capture(tmp_path, expected):
    storage = Storage(tmp_path / "cas.sqlite")
    row = DescriptiveMaterialization(
        1, "0" * 64, "age", "edition:1", "fr:editions",
        "plugin.age.alpha", "1.0.0", "schema.age", "13+", False, (),
    )
    called = []
    def capture(db):
        called.append(True)
        raise AssertionError("must not capture")
    with pytest.raises(ValueError, match="^invalid_descriptive_cas$"):
        publish_descriptive_materialization(
            storage, row, expected_execution_key=expected,
            capture_current_txn=capture,
        )
    assert called == []
