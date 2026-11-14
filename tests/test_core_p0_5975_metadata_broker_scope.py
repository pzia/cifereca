"""#5975/#5851: public metadata reader never borrows mutable GTK identity.

Synthetic two-identity library with identical physical artefact IDs. No GTK,
provider network, scans or SQLite. Installed plugins must supply identity_id.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import MetadataBrokerImpl
from cifereca.plugin_api.brokers import MetadataBroker


class ScopedService:
    def __init__(self):
        self.current_identity = NS(identity_id="bob")
        self.calls = []

    def library_entries(self, *, identity_id=None, scan=False):
        self.calls.append((identity_id, scan))
        assert scan is False
        return [{"identity_id": identity_id, "artifact_id": "shared-sha",
                 "title": "Private title for " + identity_id}]


def test_same_artifact_across_two_profiles_is_never_merged_by_gtk_navigation():
    service = ScopedService()
    broker = MetadataBrokerImpl(service)
    alice = broker.library_entries(identity_id="alice")
    assert alice == [{"identity_id": "alice", "artifact_id": "shared-sha",
                      "title": "Private title for alice"}]
    service.current_identity.identity_id = "alice"
    bob = broker.library_entries(identity_id="bob", scan=False)
    assert bob[0]["identity_id"] == "bob"
    assert alice != bob
    assert service.calls == [("alice", False), ("bob", False)]


def test_absent_identity_never_enters_private_library_projection():
    service = ScopedService()
    broker = MetadataBrokerImpl(service)
    with pytest.raises(TypeError):
        broker.library_entries()
    assert service.calls == []


@pytest.mark.parametrize("bad", [None, "", " ", "\t", 0, False, {}, [], object()])
def test_invalid_identity_rejected_before_any_read_or_scan(bad):
    service = ScopedService()
    with pytest.raises(ValueError, match="identity_id_required"):
        MetadataBrokerImpl(service).library_entries(identity_id=bad)
    assert service.calls == []


@pytest.mark.parametrize("scan", [None, 0, 1, "", "true", object(), []])
def test_scan_flag_cannot_coerce_truthy_values_into_filesystem_work(scan):
    service = ScopedService()
    with pytest.raises(ValueError, match="library_scan_flag_invalid"):
        MetadataBrokerImpl(service).library_entries(identity_id="alice", scan=scan)
    assert service.calls == []


def test_public_metadata_protocol_requires_explicit_identity_keyword_only():
    for cls in (MetadataBroker, MetadataBrokerImpl):
        signature = inspect.signature(cls.library_entries)
        owner = signature.parameters["identity_id"]
        assert owner.default is inspect.Parameter.empty
        assert owner.kind is inspect.Parameter.KEYWORD_ONLY
    assert isinstance(MetadataBrokerImpl(ScopedService()), MetadataBroker)
