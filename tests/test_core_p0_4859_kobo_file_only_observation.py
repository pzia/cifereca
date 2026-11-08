"""P0 #4859: historical Kobo W4 byte-only observations are retired."""
from __future__ import annotations

from pathlib import Path
import pytest
from cifereca.core import kobo_local_reconciliation as repair


@pytest.mark.parametrize("flag,reason", [
    (True, "legacy_file_observation_retired"),
    (False, "legacy_reconciliation_retired"),
])
def test_retired_observer_rejects_before_profile_or_sqlite(monkeypatch, flag, reason):
    monkeypatch.setattr(repair, "config_file",
                        lambda **kw: pytest.fail("cannot open Config"))
    monkeypatch.setattr(repair, "database_file",
                        lambda **kw: pytest.fail("cannot open SQLite"))
    with pytest.raises(repair.ReconciliationRejected, match="^" + reason + "$"):
        repair.reconcile_existing_profile(
            file_only=flag, identity_id="alice", credential_id="vault",
            account_id="", item_id="edition", expected_sha256="a" * 64,
            expected_size=10, path="/synthetic/book.epub",
        )


@pytest.mark.parametrize("value", (None, "true", 1, []))
def test_invalid_file_only_dispatch_rejected_before_storage(monkeypatch, value):
    monkeypatch.setattr(repair, "config_file",
                        lambda **kw: pytest.fail("cannot open Config"))
    with pytest.raises(repair.ReconciliationRejected, match="invalid_request"):
        repair.reconcile_existing_profile(file_only=value)


def test_w4_file_only_executor_is_not_installed():
    root = Path(__file__).resolve().parents[1]
    content = (root / "src/cifereca/core/kobo_local_reconciliation.py").read_text()
    assert "def observe_kobo_local_file_only(" not in content
    assert "_FILE_ONLY_SCHEMA" not in content
    assert "_FILE_ONLY_JOURNAL" not in content
