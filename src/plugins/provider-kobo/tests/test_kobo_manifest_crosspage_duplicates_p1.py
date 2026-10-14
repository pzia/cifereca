"""#1346: a Kobo manifest cannot convey duplicate personal editions to Core.

The synthetic /library/manifest can replay rows across cursors or return
multiple revisions. All ambiguous variants are excluded before materialization.
No live Kobo account, file write, download, acquisition or DRM operation.
"""
from __future__ import annotations

import json

import httpx

from cifereca_kobo_vault import KoboVaultClient
from tests.test_kobo_manifest_policy_p1 import session_with_vault


def _client(responses):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.method == "GET"
        assert request.url.path == "/api/v2/library/manifest"
        index = len(calls) - 1
        assert index < len(responses), "Unexpected extra page request"
        return httpx.Response(200, json=responses[index])

    return (KoboVaultClient(
        "https://vault.example", "PRIVATE_API_TOKEN",
        transport=httpx.MockTransport(handler),
    ), calls)


def test_duplicate_editions_are_quarantined_across_cursor_pages_and_aliases():
    client, calls = _client([
        {
            "items": [
                {"product_id": "PRIVATE_REPEATED", "revision_id": "PRIVATE_R1",
                 "owned": True, "downloadable": True, "account_id": "alice"},
                {"product_id": "stable-one", "owned": True},
                {"id": "PRIVATE_LEGACY", "revision_id": "PRIVATE_L1"},
            ],
            "next_cursor": "PRIVATE_CURSOR",
        },
        {
            "items": [
                {"id": "PRIVATE_REPEATED", "revision_id": "PRIVATE_R2",
                 "owned": False, "account_id": "alice"},
                {"product_id": "PRIVATE_LEGACY", "id": "PRIVATE_LEGACY",
                 "revision_id": "PRIVATE_L2"},
                {"product_id": "stable-two", "account_id": "alice"},
            ],
            "next_cursor": None,
        },
    ])
    try:
        rows = client.library_manifest()
        assert [item.get("product_id") or item.get("id") for item in rows] == [
            "stable-one", "stable-two",
        ]
        assert client.last_manifest_rejections == [
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
        ]
        assert len(calls) == 2
        assert "cursor=PRIVATE_CURSOR" in calls[1]
        exported = json.dumps(client.last_manifest_rejections)
        for private in (
            "PRIVATE_", "alice", "PRIVATE_R1", "PRIVATE_R2", "Bearer",
            "stable-one", "stable-two", "revision_id", "downloadable",
        ):
            assert private not in exported
    finally:
        client.close()


def test_duplicate_identical_rows_are_ambiguous_and_rejections_reset():
    state = {"duplicate": True}

    def handler(request):
        assert request.url.path == "/api/v2/library/manifest"
        item = {"product_id": "edition-1", "owned": True, "downloadable": True}
        rows = [item, dict(item)] if state["duplicate"] else [item]
        return httpx.Response(200, json={"items": rows, "next_cursor": None})

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_API_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        assert client.library_manifest() == []
        assert client.last_manifest_rejections == [
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
            {"type": "invalid_scope", "reason": "duplicate_product_id"},
        ]
        state["duplicate"] = False
        assert [row["product_id"] for row in client.library_manifest()] == [
            "edition-1",
        ]
        assert client.last_manifest_rejections == []
    finally:
        client.close()


def test_contradictory_alias_poisoning_wins_over_duplicate_diagnostic():
    client, _ = _client([{
        "items": [
            {"product_id": "alias-a", "id": "alias-b",
             "revision_id": "PRIVATE_BAD"},
            {"product_id": "alias-a", "revision_id": "PRIVATE_A"},
            {"id": "alias-b", "revision_id": "PRIVATE_B"},
            {"product_id": "alias-a", "revision_id": "PRIVATE_A2"},
            {"product_id": "unrelated", "owned": True},
        ],
        "next_cursor": None,
    }])
    try:
        assert [row["product_id"] for row in client.library_manifest()] == [
            "unrelated",
        ]
        assert client.last_manifest_rejections == [
            {"type": "invalid_shape", "reason": "unverifiable_product_id"},
            {"type": "invalid_shape", "reason": "tainted_product_id"},
            {"type": "invalid_shape", "reason": "tainted_product_id"},
            {"type": "invalid_shape", "reason": "tainted_product_id"},
        ]
        assert "PRIVATE" not in json.dumps(client.last_manifest_rejections)
    finally:
        client.close()


def test_session_backup_policy_marks_partial_manifest_after_duplicate_quarantine():
    client, calls = _client([{
        "items": [
            {"product_id": "risky", "revision_id": "PRIVATE_REV1",
             "owned": True, "downloadable": True, "account_id": "alice"},
            {"product_id": "risky", "revision_id": "PRIVATE_REV2",
             "owned": True, "downloadable": True, "account_id": "alice"},
            {"product_id": "clean", "owned": True, "account_id": "alice"},
        ],
        "next_cursor": None,
    }])
    session = session_with_vault(client)
    try:
        rows = session.backup_manifest()
        assert [item["product_id"] for item in rows] == ["clean"]
        assert session.backup_manifest_rejection_count == 2
        policy = session.backup_manifest_policy
        assert policy["manifest_rejections"] == 2
        assert policy["manifest_authoritative"] is False
        assert policy["acquisition_authorized"] is False
        assert policy["status"] == "incomplete"
        assert len(calls) == 1
        diagnostic = json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
        for private in ("PRIVATE_", "alice", "risky", "clean", "Bearer"):
            assert private not in diagnostic
    finally:
        session.close()
