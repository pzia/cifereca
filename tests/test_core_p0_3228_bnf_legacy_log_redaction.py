"""P0 #3228: the historical BnF worker must not log borrower identifiers or exceptions.

Static source guard plus an actual failing receipt-file probe; no remote BnF
request, login, ACSM, ADEPT or real identity is required.
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
import re
from types import SimpleNamespace

from cifereca.core.bnf_auto_materialization import _receipt_local_file_verified
from cifereca.models import Account


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "src" / "cifereca" / "core" / "bnf_auto_materialization.py"
PUBLIC_LINE = re.compile(
    r"TRACE bnf_auto code=[a-z_]+(?: stage=[a-z_]+)?"
)


def test_all_historical_bnf_worker_logs_are_static_closed_codes():
    source = ast.parse(WORKER.read_text(encoding="utf-8"))
    audited = 0
    for node in ast.walk(source):
        if not isinstance(node, ast.Call):
            continue
        call = node.func
        if (not isinstance(call, ast.Attribute)
                or not isinstance(call.value, ast.Name)
                or call.value.id != "_TRACE_LOG"):
            continue
        assert call.attr in {"info", "warning"}
        assert len(node.args) == 1
        assert isinstance(node.args[0], ast.Constant)
        assert type(node.args[0].value) is str
        assert PUBLIC_LINE.fullmatch(node.args[0].value)
        assert not node.keywords  # exc_info=True can leak private paths/URLs
        audited += 1
    assert audited == 8  # lock every legacy trace entry to this contract


def test_os_error_never_echoes_path_account_credential_or_traceback(caplog):
    private = "PRIVATE-PATH-URL-CSRF-ACCOUNT-DO-NOT-LOG"
    account = Account(
        identity_id="IDENTITY-" + private, provider_id="bnf-pret",
        credential_id="CREDENTIAL-" + private, unique_id="BORROWER-" + private,
    )

    def unavailable(_artifact):
        raise OSError("https://pret.bnf.fr/token?csrf=" + private)

    service = SimpleNamespace(
        storage=SimpleNamespace(artifact_get=unavailable),
    )
    row = {
        "identity_id": account.identity_id,
        "provider_id": "manual", "credential_id": "manual",
        "artifact_id": "sha256:" + "a" * 64,
    }
    with caplog.at_level(logging.WARNING, logger="cifereca"):
        assert _receipt_local_file_verified(
            service, account, row, Path("/not-present/" + private),
        ) is False
    logs = [record.getMessage() for record in caplog.records]
    assert logs == ["TRACE bnf_auto code=receipt_file_check_failed"]
    assert private not in caplog.text
    assert "https://" not in caplog.text
    assert "Traceback" not in caplog.text
