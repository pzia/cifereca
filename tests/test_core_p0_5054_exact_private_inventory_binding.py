"""P0 #5054: exact private inventory binding is provider neutral, not a right."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.core.private_inventory_binding import exact_private_inventory_binding_valid
from cifereca.models import Account
from tests.test_sync_state_contract import _service


def _bound(service, provider="synthetic-books"):
    account = Account(
        display_name="Synthetic", unique_id="borrower-a",
        provider_id=provider, credential_id="credential-a", identity_id="default",
    )
    service.accounts.append(account)
    connection = service.storage.provider_connection_put({
        "identity_id": "default", "provider_id": provider,
        "credential_id": "credential-a", "enabled": True,
    })
    service.storage.provider_account_put({
        "connection_id": connection, "identity_id": "default",
        "provider_id": provider, "credential_id": "credential-a",
        "account_id": "borrower-a", "label": "Synthetic",
    })
    session = NS(
        provider_id=provider, credential_id="credential-a",
        connected=True, capabilities=NS(personal_library=True),
    )
    service._sessions[service._session_key(provider, "credential-a")] = session
    return account, session


def test_generic_private_binding_checks_exact_registered_borrower(tmp_path):
    service = _service(tmp_path)
    try:
        account, session = _bound(service)
        assert exact_private_inventory_binding_valid(
            service, account, session=session, provider_id="synthetic-books",
        )
        assert not exact_private_inventory_binding_valid(
            service, account, session=session, provider_id="numilog",
        )
        assert not exact_private_inventory_binding_valid(
            service, account, session=session, provider_id="",
        )
        assert not exact_private_inventory_binding_valid(
            service, account, session=session, provider_id=object(),
        )
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


@pytest.mark.parametrize("failure", [
    "detached", "rebound", "disabled", "duplicate_account",
    "replaced_session", "duplicate_session",
])
def test_binding_fails_closed_without_supplier_io(tmp_path, failure):
    service = _service(tmp_path)
    try:
        account, session = _bound(service)
        if failure == "detached":
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "DELETE FROM provider_accounts WHERE provider_id=? AND credential_id=?",
                    ("synthetic-books", "credential-a"),
                )
        elif failure == "rebound":
            foreign = service.storage.provider_connection_put({
                "identity_id": "alex", "provider_id": "synthetic-books",
                "credential_id": "credential-a", "enabled": True,
            })
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "UPDATE provider_accounts SET connection_id=? "
                    "WHERE provider_id=? AND credential_id=? AND account_id=?",
                    (foreign, "synthetic-books", "credential-a", "borrower-a"),
                )
        elif failure == "disabled":
            service.storage.provider_connection_put({
                "identity_id": "default", "provider_id": "synthetic-books",
                "credential_id": "credential-a", "enabled": False,
            })
        elif failure == "duplicate_account":
            service.accounts.append(Account(
                display_name="Second", unique_id="borrower-b",
                provider_id="synthetic-books", credential_id="credential-a",
                identity_id="default",
            ))
        elif failure == "replaced_session":
            service._sessions[service._session_key("synthetic-books", "credential-a")] = NS(
                provider_id="synthetic-books", credential_id="credential-a",
                connected=True, capabilities=NS(personal_library=True),
            )
        else:
            service._sessions[("duplicate", "credential-a")] = NS(
                provider_id="synthetic-books", credential_id="credential-a",
                connected=True, capabilities=NS(personal_library=True),
            )
        assert not exact_private_inventory_binding_valid(
            service, account, session=session, provider_id="synthetic-books",
        )
        assert service.storage.acquisitions() == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_manual_admission_does_not_import_numilog_binding():
    root = Path(__file__).resolve().parents[1]
    file = root / "src/cifereca/core/numilog_manual_acquisition.py"
    source = file.read_text(encoding="utf-8")
    assert "numilog_inventory_binding_valid" not in source
    assert "exact_private_inventory_binding_valid" in source
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "numilog_manifest_rights"
        and any(alias.name == "numilog_inventory_binding_valid" for alias in node.names)
        for node in ast.walk(ast.parse(source))
    )
