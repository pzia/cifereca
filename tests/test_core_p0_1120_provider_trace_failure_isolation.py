"""#1120: provider download telemetry must not override a completed result."""
from __future__ import annotations

import logging
import time
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.core.loan_provider_delivery import acquire_provider_loan


PRIVATE = "https://private.invalid/signed.acsm?auth=DO_NOT_LOG"


class HostileString(str):
    def __eq__(self, _other):
        raise AssertionError(PRIVATE)


class HostilePath:
    def __bool__(self):
        raise AssertionError(PRIVATE)

    def is_file(self):
        raise AssertionError(PRIVATE)

    def stat(self):
        raise AssertionError(PRIVATE)


class HostileAcquisition:
    def __init__(self, invalid):
        self.invalid = invalid

    def __getattribute__(self, name):
        if name in {"kind", "path"} and object.__getattribute__(self, "invalid") == name:
            raise RuntimeError(PRIVATE)
        return object.__getattribute__(self, name)

    @property
    def kind(self):
        return "acsm"

    @property
    def path(self):
        return HostilePath()


@pytest.mark.parametrize("kind", ["kind", "path", "untrusted_kind", "untrusted_path", "valid"])
def test_provider_result_is_returned_even_when_trace_fields_are_untrusted(
    tmp_path, caplog, kind,
):
    file = tmp_path / "synthetic-acsm"
    file.write_bytes(b"fixture")
    if kind in {"kind", "path"}:
        acquisition = HostileAcquisition(kind)
    elif kind == "untrusted_kind":
        acquisition = NS(kind=HostileString("acsm"), path=file)
    elif kind == "untrusted_path":
        acquisition = NS(kind="acsm", path=HostilePath())
    else:
        acquisition = NS(kind="acsm", path=file)
    provider = Mock(return_value=acquisition)
    loan = NS(id="loan", source_provider="paris", source_credential="credential",
              source_account_id="linked", record_id="record")
    logger = logging.getLogger("cifereca.tests.loan-provider")
    with caplog.at_level(logging.INFO, logger=logger.name):
        outcome = acquire_provider_loan(
            NS(acquire_digital_loan=provider), loan, force_refresh=False,
            time=time, _TRACE_LOG=logger, identity_id="borrower",
        )
    assert outcome is acquisition
    provider.assert_called_once_with(loan)
    messages = [row.getMessage() for row in caplog.records
                if "provider" in row.getMessage()]
    assert len(messages) == 2
    assert "loan_ref=" in messages[-1]
    assert "kind=unknown" in messages[-1] if kind in {
        "kind", "untrusted_kind",
    } else "kind=acsm" in messages[-1]
    expected_size = len(b"fixture") if kind in {"untrusted_kind", "valid"} else 0
    assert f"bytes={expected_size}" in messages[-1]
    assert PRIVATE not in caplog.text


def test_provider_exception_still_propagates_without_post_delivery_logging(caplog):
    failure = RuntimeError(PRIVATE)
    provider = Mock(side_effect=failure)
    loan = NS(id="loan", source_provider="paris", source_credential="credential",
              source_account_id="linked", record_id="record")
    logger = logging.getLogger("cifereca.tests.loan-provider")
    with caplog.at_level(logging.INFO, logger=logger.name):
        with pytest.raises(RuntimeError) as caught:
            acquire_provider_loan(
                NS(acquire_digital_loan=provider), loan,
                force_refresh=True, time=time, _TRACE_LOG=logger,
            )
    assert caught.value is failure
    provider.assert_called_once_with(loan, force_refresh=True)
    assert not any("provider end" in row.getMessage() for row in caplog.records)
    assert PRIVATE not in caplog.text
