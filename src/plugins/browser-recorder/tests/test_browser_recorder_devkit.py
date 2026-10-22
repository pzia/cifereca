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
    assert json.loads(result["plugin-dev-kit/contract-registry.json"]) == json.loads(
        json.dumps([asdict(row) for row in CONTRACT_REGISTRY])
    )
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



@pytest.mark.parametrize("exchange_count", (0, 1, 32))
def test_zip_offline_sdk_handles_zero_one_many_requests_and_missing_bodies(
    exchange_count, tmp_path, monkeypatch,
):
    """#2740: LLM-ready contracts even when pages expose no JSON/DOM at all.

    Only observed exchanges are evidence. Zero capture does NOT imply a
    provider is unreachable or lacks an API. Zero network calls during
    preparation/review/export (except test's local filesystem writes).
    """
    from io import BytesIO
    from zipfile import ZipFile
    import socket

    origin = "https://fictional-llm-fixture.example.org"
    trace = PrivateHttpTrace((origin,))
    for number in range(1, exchange_count + 1):
        # Deliberately omit request/response text and DOM evidence. Each
        # observation still has a deterministic exchange ID and status.
        trace.exchanges.append(PrivateExchange(
            number, "GET", origin + "/catalog?page=2&limit=20",
            status=200 if number % 2 else 304,
            mime="application/json" if number % 3 else "text/html",
            response_body=None,
            response_body_state="not_captured",
        ))
    review = IdentifierReview(trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
    def forbid(*_args, **_kwargs):
        raise AssertionError("offline SDK zip must not open sockets")
    monkeypatch.setattr(socket, "socket", forbid)
    plan = prepare_reviewed_zip(review)
    preview = plan.preview()
    names = {row["name"] for row in preview["members"]}
    assert "plugin-dev-kit/manifest.json" in names
    assert "plugin-dev-kit/LLM_GUIDE.md" in names
    assert "plugin-dev-kit/examples/provider-fixture-example/cifereca-plugin.json" in names
    assert "timeline.json" in names
    assert "manifest.json" in names
    assert {
        f"exchanges/{i:04d}.json" for i in range(1, exchange_count + 1)
    } == {name for name in names if name.startswith("exchanges/")}
    assert not any(name.startswith("bodies/") for name in names)

    payload = dict(plan._members)
    manifest = json.loads(payload["manifest.json"])
    assert manifest["exchange_count"] == exchange_count
    assert len(json.loads(payload["timeline.json"])) == exchange_count
    assert manifest["coverage"]
    kit_manifest = json.loads(payload["plugin-dev-kit/manifest.json"])
    assert kit_manifest["plugin_api_version"] == PLUGIN_API_VERSION
    for source in _PUBLIC_SOURCES:
        target = "plugin-dev-kit/contracts/" + source
        assert payload[target] == resources.files(
            "cifereca.plugin_api"
        ).joinpath(source).read_bytes()
    for item in preview["members"]:
        assert item["bytes"] == len(payload[item["name"]])
        assert item["sha256"] == sha256(payload[item["name"]]).hexdigest()
    output = tmp_path / f"fictional-offline-{exchange_count}.zip"
    plan.save_as(output, user_confirmed=True)
    with ZipFile(output) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == names
        for item in preview["members"]:
            assert archive.read(item["name"]) == payload[item["name"]]
        assert archive.read("timeline.json") == payload["timeline.json"]
    assert not any(name.endswith(".har") for name in names)
    assert not any(name.startswith("storage_state") for name in names)
    assert not any("cookie" in name for name in names)
    trace.destroy()
