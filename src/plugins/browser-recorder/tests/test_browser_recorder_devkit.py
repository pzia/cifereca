"""Offline SDK/documentation bundle uses only the real PUBLIC running Core.

No provider connection, login, private HTTP source or network resource.
"""
from __future__ import annotations

from dataclasses import asdict
from hashlib import sha256
from importlib import resources
import json
import socket

import pytest

from browser_recorder import RecorderError
from browser_recorder_devkit import _PUBLIC_SOURCES, core_contract_kit
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip
from cifereca.plugin_api import (
    CONTRACT_REGISTRY, MANIFEST_VERSION, PLUGIN_API_VERSION,
)


def test_kit_embeds_exact_public_contracts_and_provenance():
    result = core_contract_kit()
    manifest = json.loads(result["plugin-dev-kit/manifest.json"])
    assert manifest["schema"] == "cifereca.browser-recorder.plugin-dev-kit/1"
    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["plugin_api_version"] == PLUGIN_API_VERSION
    assert manifest["core_version"]
    assert manifest["generated_at_utc"]
    assert manifest["git_revision"] == "unknown"  # no invented revision
    assert json.loads(result["plugin-dev-kit/contract-registry.json"]) == [
        asdict(row) for row in CONTRACT_REGISTRY
    ]
    declared = manifest["files"]
    assert len(declared) == len(result) - 1
    assert {row["path"] for row in declared} == set(result) - {
        "plugin-dev-kit/manifest.json"
    }
    for source in _PUBLIC_SOURCES:
        target = "plugin-dev-kit/contracts/" + source
        from_running_core = resources.files("cifereca.plugin_api").joinpath(
            source
        ).read_bytes()
        assert result[target] == from_running_core
        assert target in {row["path"] for row in declared}
        assert b"FAKE_PRIVATE_ACCOUNT" not in result[target]
    for row in declared:
        body = result[row["path"]]
        assert row["sha256"] == sha256(body).hexdigest()
        assert row["bytes"] == len(body)
        assert row["source"] and not row["source"].startswith("/")
    assert b"PluginContext" in result["plugin-dev-kit/README.md"]
    assert b"~route-NNNN~" in result["plugin-dev-kit/README.md"]
    assert sum(map(len, result.values())) < 550_000


def test_core_public_kit_does_not_require_network(monkeypatch):
    # Core modules were imported above; block all newly attempted sockets.
    def forbidden(*_a, **_kw):
        raise AssertionError("contract kit must not open sockets")
    monkeypatch.setattr(socket, "socket", forbidden)
    result = core_contract_kit()
    assert result["plugin-dev-kit/contracts/context.py"]


def test_missing_public_contract_fails_closed(monkeypatch):
    import browser_recorder_devkit as sdk

    class BrokenPackage:
        def joinpath(self, _name):
            raise FileNotFoundError("never echo this pathname")

    monkeypatch.setattr(sdk.resources, "files", lambda _package: BrokenPackage())
    with pytest.raises(RecorderError, match="core_kit_unavailable"):
        sdk.core_contract_kit()


def test_reviewed_zip_contains_sdk_in_exact_preview():
    origin = "https://fictional-books.example.org"
    raw = PrivateHttpTrace((origin,))
    raw.exchanges.append(PrivateExchange(
        1, "GET", url=origin + "/api/catalog?page=2&limit=20",
        status=200, mime="application/json",
        response_body='{"ProductId":214,"status":"ready"}',
        response_body_state="captured",
    ))
    review = IdentifierReview(raw)
    for c in review.candidates():
        review.decide(c.key, "anonymize")
    plan = prepare_reviewed_zip(review)
    preview = plan.preview()
    docs = [row for row in preview["members"] if row["name"].startswith("plugin-dev-kit/")]
    assert docs and len(docs) > len(_PUBLIC_SOURCES)
    names = {row["name"] for row in docs}
    assert "plugin-dev-kit/manifest.json" in names
    assert "plugin-dev-kit/contracts/models.py" in names
    assert "plugin-dev-kit/LLM_GUIDE.md" in names
    assert not any("FAKE_PRIVATE_ACCOUNT" in row["text"] for row in docs)
    manifest = next(
        row for row in docs if row["name"] == "plugin-dev-kit/manifest.json"
    )
    declared = json.loads(manifest["text"])["files"]
    lookup = {row["name"]: row for row in docs}
    for entry in declared:
        assert entry["path"] in lookup
        assert sha256(lookup[entry["path"]]["text"].encode()).hexdigest() == entry["sha256"]
    raw.destroy()
