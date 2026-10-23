"""#2888: explain missing loan action without logging, network or private data."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.loan_download_status import loan_download_unavailable_message
from cifereca.services import LibraryService


def _service(session=None, *, error=None):
    # No Core startup or provider request necessary to classify a local
    # connected-session state.
    service = object.__new__(LibraryService)
    if error is not None:
        service._session_for_source = Mock(side_effect=error)
    else:
        service._session_for_source = Mock(return_value=session)
    return service


def _loan(medium="digital"):
    return NS(
        medium=medium, source_provider="paris",
        source_credential="SECRET_CREDENTIAL",
    )


@pytest.mark.parametrize("session,code", [
    (NS(connected=False, capabilities=NS(digital_download=True)),
     "provider_disconnected"),
    (NS(connected=True, capabilities=NS(digital_download=False)),
     "download_unsupported"),
    (NS(connected=True, capabilities=NS(digital_download=True)),
     "available"),
])
def test_eligibility_classifies_only_existing_session_evidence(session, code):
    service = _service(session)
    loan = _loan()
    result = service.loan_download_eligibility(loan)
    assert result == {"available": code == "available", "code": code}
    assert service.can_download_loan(loan) is (code == "available")
    assert service._session_for_source.call_count == 2
    assert service._session_for_source.call_args.args == (
        "paris", "SECRET_CREDENTIAL",
    )
    assert "SECRET_CREDENTIAL" not in json.dumps(result)


def test_physical_loan_never_looks_up_session():
    service = _service(error=AssertionError("MUST NOT LOOK UP SESSION"))
    loan = _loan("physical")
    assert service.loan_download_eligibility(loan) == {
        "available": False, "code": "not_digital",
    }
    assert not service.can_download_loan(loan)
    service._session_for_source.assert_not_called()


def test_failed_lookup_hides_private_exception(caplog):
    private = "PRIVATE_READER_EMAIL@example.test?token=SECRET"
    service = _service(error=RuntimeError(private))
    result = service.loan_download_eligibility(_loan())
    assert result == {"available": False, "code": "provider_unavailable"}
    assert private not in repr(result) + caplog.text
    assert private not in loan_download_unavailable_message(result)
    service._session_for_source.assert_called_once()


def test_invalid_session_capabilities_never_claim_available():
    service = _service(NS(connected=True, capabilities=None))
    assert service.loan_download_eligibility(_loan()) == {
        "available": False, "code": "provider_unavailable",
    }


@pytest.mark.parametrize("result", [
    {"available": False, "code": "reader@example.test"},
    {"available": False, "code": "provider_disconnected", "url": "PRIVATE"},
    {"available": False, "code": 27},
    {"available": "false", "code": "provider_disconnected"},
    {"available": True, "code": "provider_disconnected"},
    {},
    "provider_disconnected",
    None,
])
def test_ui_refuses_unknown_or_untrusted_reason_fields(result):
    assert loan_download_unavailable_message(result) is None


def test_ui_reasons_distinguish_disconnected_from_absent_or_unsupported():
    results = {
        k: loan_download_unavailable_message({"available": False, "code": k})
        for k in ("provider_disconnected", "provider_unavailable",
                  "download_unsupported", "not_digital")
    }
    assert all(isinstance(value, str) for value in results.values())
    assert len(set(results.values())) == 4
    assert "Reconnectez" in results["provider_disconnected"]
    assert "n’annonce pas" in results["download_unsupported"]
    assert "SECRET" not in json.dumps(results)


def test_detail_panel_explains_refused_action_without_starting_download():
    # GTK is optional in the generic Core CI. Assert the GTK method's
    # contract while its real GTK acceptance stays a distinct required gate.
    source = (
        Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef)
               and n.name == "DetailPanel")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef)
                  and n.name == "show_loan")
    text = ast.unparse(method)
    assert "loan_download_unavailable_message" in text
    assert '"Téléchargement du prêt"' in text
    assert "self.window.service.can_download_loan(loan)" in text
    assert "borrow_digital" not in text
    assert "download_digital" not in text
