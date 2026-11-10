"""#3196: source phase history must not import retired W2 execution machinery."""
from __future__ import annotations

import ast
import json
from pathlib import Path

from cifereca.core import kobo_backup
from cifereca.core.legacy_checkpoint_read import (
    _kobo_checkpoint_for, _kobo_checkpoint_key,
)


def test_w2_keeps_exact_checkpoint_compatibility_symbols():
    assert kobo_backup._kobo_checkpoint_for is _kobo_checkpoint_for
    assert kobo_backup._kobo_checkpoint_key is _kobo_checkpoint_key


def test_checkpoint_reader_is_read_only_and_exactly_scoped():
    class Storage:
        def __init__(self):
            self.records = {}
            self.reads = []
        def metadata_get(self, key):
            self.reads.append(key)
            return self.records.get(key)
    class Service:
        storage = Storage()

    service = Service()
    first = _kobo_checkpoint_key("identity-a", "credential-a")
    second = _kobo_checkpoint_key("identity-a", "credential-b")
    third = _kobo_checkpoint_key("identity-b", "credential-a")
    assert len({first, second, third}) == 3
    service.storage.records[first] = json.dumps({
        "schema_version": 1, "status": "partial", "updated_at": 123456789,
    })
    service.storage.records[second] = json.dumps({
        "schema_version": 2, "status": "complete",
    })
    assert _kobo_checkpoint_for(service, "identity-a", "credential-a") == {
        "schema_version": 1, "status": "partial", "updated_at": 123456789,
    }
    assert _kobo_checkpoint_for(service, "identity-a", "credential-b") is None
    assert _kobo_checkpoint_for(service, "identity-b", "credential-a") is None
    assert service.storage.reads == [first, second, third]
    assert len(service.storage.records) == 2


def test_malformed_offline_checkpoint_remains_untrusted():
    class Service:
        class storage:
            @staticmethod
            def metadata_get(_key):
                return "{malformed private json"
    assert _kobo_checkpoint_for(Service(), "identity", "credential") is None


def test_phase_broker_does_not_import_retired_w2_reader():
    file = Path(__file__).resolve().parents[1] / "src/cifereca/brokers.py"
    imports = [
        (node.module, tuple(alias.name for alias in node.names))
        for node in ast.walk(ast.parse(file.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom)
    ]
    assert ("core.legacy_checkpoint_read", ("_kobo_checkpoint_for",)) in imports
    assert not any(
        module == "core.kobo_backup" and "_kobo_checkpoint_for" in names
        for module, names in imports
    )
