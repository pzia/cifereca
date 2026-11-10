"""The removed Numilog v1 preview cannot authorize local or provider I/O."""
from types import SimpleNamespace

from cifereca.core.numilog_local_preview import preview


def test_retired_preview_fails_closed_without_touching_its_broker_or_account():
    class Inaccessible:
        def __getattribute__(self, name):
            raise AssertionError("retired preview accessed caller state")

    for verify in (False, True):
        receipt = preview(Inaccessible(), Inaccessible(), "unknown", verify_local_files=verify)
        assert not receipt.ok
        assert receipt.changed == 0
        assert receipt.facts["status"] == "preview_unavailable"
        assert receipt.facts["read_only"] is True
        assert receipt.facts["acquisition_authorized"] is False
        assert "counts" not in receipt.facts
