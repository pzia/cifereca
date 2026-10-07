from __future__ import annotations

from pathlib import Path

from cifereca.artifact_inspection import inspect_artifact
from cifereca.plugin_api import TransportService, check_service_contract


class GoodTransport:
    def matches(self, source):
        return True

    def fetch(self, source, destination):
        return Path(destination)


class BadTransport:
    def fetch(self, source, destination):
        return Path(destination)


def test_transport_contract_is_structural():
    assert isinstance(GoodTransport(), TransportService)
    assert check_service_contract("transport", GoodTransport()).ok
    check = check_service_contract("transport", BadTransport())
    assert not check.ok
    assert check.reason_code == "contract_mismatch"

