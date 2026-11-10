"""#3196: broker's historical checkpoint observation does not load retired W2."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

from cifereca.core import kobo_backup
from cifereca.core import legacy_checkpoint_reader as reader


def test_historical_checkpoint_helpers_keep_exact_abi():
    assert kobo_backup._kobo_checkpoint_key is reader._kobo_checkpoint_key
    assert kobo_backup._kobo_checkpoint_for is reader._kobo_checkpoint_for
    assert reader._kobo_checkpoint_key("id", "credential") == (
        kobo_backup._kobo_checkpoint_key("id", "credential")
    )


def test_broker_observation_imports_pure_reader_not_w2_engine():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/brokers.py").read_text(
        encoding="utf-8"
    )
    imports = [
        (node.module, {alias.name for alias in node.names})
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    ]
    for module, names in imports:
        if "_kobo_checkpoint_for" in names:
            assert module == "core.legacy_checkpoint_reader"


def test_checkpoint_is_a_read_only_observation_never_positive_right():
    store = {}
    service = SimpleNamespace(storage=SimpleNamespace(
        metadata_get=lambda key: store.get(key),
    ))
    assert reader._kobo_checkpoint_for(service, "reader", "credential") is None
    key = reader._kobo_checkpoint_key("reader", "credential")
    store[key] = json.dumps({"schema_version": 1, "status": "complete",
                              "counts": {"downloaded": 99},
                              "private": "/home/alice/book.epub"})
    observed = reader._kobo_checkpoint_for(service, "reader", "credential")
    assert observed["status"] == "complete"
    assert observed["private"] == "/home/alice/book.epub"
    store[key] = '{"schema_version": 2, "status": "complete"}'
    assert reader._kobo_checkpoint_for(service, "reader", "credential") is None
    store[key] = "not valid json"
    assert reader._kobo_checkpoint_for(service, "reader", "credential") is None
