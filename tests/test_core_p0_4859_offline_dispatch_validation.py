"""#4859: malformed offline W4/D5 dispatch refuses before private profile I/O.

Only a synthetic argument contract is tested; no Config, SQLite or file is
opened, and no historical reconciliation is executed.
"""
from __future__ import annotations

import pytest

from cifereca.core import kobo_local_reconciliation as repair


FILE_ONLY = {
    "identity_id": "alice", "credential_id": "vault",
    "account_id": "", "item_id": "edition",
    "expected_sha256": "a" * 64, "expected_size": 100,
    "path": "/synthetic/alice/book.epub",
}
CANONICAL = {
    "identity_id": "alice", "credential_id": "vault",
    "account_id": "", "edition_id": "edition",
    "right_id": "right", "branch_id": "owned",
    "placement_id": "placement",
    "path": "/synthetic/alice/book.epub",
}


@pytest.mark.parametrize("dispatch", (
    {"canonical_relink": True, **CANONICAL, "undo_operation": "legacy-undo"},
    {"canonical_relink": True, **CANONICAL, "context_current": lambda: True},
    {"canonical_relink": True, **CANONICAL, "cancelled": lambda: False},
    {"canonical_relink": True, **{k: v for k, v in CANONICAL.items()
                                  if k != "placement_id"}},
))
def test_invalid_offline_dispatch_rejected_before_any_profile_access(
    monkeypatch, dispatch,
):
    monkeypatch.setattr(repair, "config_file",
                        lambda **kw: pytest.fail("cannot open Config"))
    monkeypatch.setattr(repair, "database_file",
                        lambda **kw: pytest.fail("cannot open SQLite"))
    with pytest.raises(repair.ReconciliationRejected, match="^invalid_request$"):
        repair.reconcile_existing_profile(**dispatch)


@pytest.mark.parametrize("extra", (
    {"undo_operation": "legacy-undo"},
    {"context_current": lambda: True},
    {"identity_id": "alice"},
))
def test_retired_file_only_dispatch_never_opens_private_data(monkeypatch, extra):
    monkeypatch.setattr(repair, "config_file",
                        lambda **kw: pytest.fail("cannot open Config"))
    monkeypatch.setattr(repair, "database_file",
                        lambda **kw: pytest.fail("cannot open SQLite"))
    with pytest.raises(repair.ReconciliationRejected,
                       match="^legacy_file_observation_retired$"):
        repair.reconcile_existing_profile(
            file_only=True, **{**FILE_ONLY, **extra},
        )


def test_retired_legacy_default_fails_before_option_or_profile_read(monkeypatch):
    monkeypatch.setattr(
        repair, "config_file",
        lambda **_kw: pytest.fail("retired W4 cannot open Config"),
    )
    with pytest.raises(
        repair.ReconciliationRejected, match="^legacy_reconciliation_retired$",
    ):
        repair.reconcile_existing_profile(undo_operation="not-an-authority")
