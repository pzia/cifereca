"""#1346: conflicting personal Kobo search hits cannot attest ownership.

Synthetic Kobo Vault search results only: no provider HTTP, acquisition, DRM,
Core entitlement updates, or private identifiers in public diagnostics.
"""
from __future__ import annotations

import json

from cifereca_kobo_vault import KoboVaultClient


def test_duplicate_personal_search_variants_keep_only_public_evidence(monkeypatch):
    vault = KoboVaultClient("https://vault.example", "PRIVATE_TOKEN")
    calls = []

    def catalog(_query, **_kwargs):
        calls.append("catalog")
        return {"items": [
            {"product_id": "shared", "title": "Public description",
             "description": "Public bibliographic fact", "owned": True},
            {"product_id": "catalog-only", "title": "Public only"},
        ]}

    def library(_query, **_kwargs):
        calls.append("library")
        return {"items": [
            {"product_id": "shared", "account_id": "PRIVATE_ALICE",
             "owned": True, "downloadable": True, "title": "PRIVATE_TITLE_A"},
            {"product_id": "shared", "account_id": "PRIVATE_BOB",
             "owned": True, "revision_id": "PRIVATE_REVISION",
             "title": "PRIVATE_TITLE_B"},
            {"product_id": "no-public", "account_id": "PRIVATE_ALICE",
             "owned": True, "revision_id": "first"},
            {"product_id": "no-public", "account_id": "PRIVATE_ALICE",
             "owned": True, "revision_id": "second"},
            {"product_id": "unambiguous", "account_id": "PRIVATE_ALICE",
             "owned": True, "title": "One personal book"},
            # Conflicting legacy aliases taint both otherwise valid products.
            {"product_id": "ALIAS_A", "id": "ALIAS_B",
             "account_id": "PRIVATE_BOB", "owned": True},
            {"product_id": "ALIAS_A", "account_id": "PRIVATE_ALICE",
             "owned": True, "title": "PRIVATE_ALIAS_A"},
            {"product_id": "ALIAS_B", "account_id": "PRIVATE_ALICE",
             "owned": True, "title": "PRIVATE_ALIAS_B"},
        ]}

    monkeypatch.setattr(vault, "search_catalog", catalog)
    monkeypatch.setattr(vault, "search_library", library)
    try:
        payload = vault.search("example", scope="all")
        rows = payload["items"]
        assert calls == ["catalog", "library"]
        assert [row["product_id"] for row in rows] == [
            "shared", "catalog-only", "unambiguous",
        ]
        public = rows[0]
        assert public["title"] == "Public description"
        assert public["description"] == "Public bibliographic fact"
        assert public["owned"] is False
        assert public["collection"] == "catalog"
        assert not any(key in public for key in (
            "account_id", "revision_id", "downloadable",
        ))
        assert rows[-1]["owned"] is True
        assert vault.last_search_merge_rejections == (
            [{"source": "library", "type": "invalid_scope",
              "reason": "duplicate_product_id"}] * 4
            + [{"source": "library", "type": "invalid_shape",
                "reason": "missing_product_id"}]
            + [{"source": "library", "type": "invalid_scope",
                "reason": "duplicate_product_id"}] * 2
        )
        assert [row["product_id"] for row in payload["library"]["items"]] == [
            "unambiguous",
        ]

        # The *accepted* unambiguous personal hit legitimately carries
        # its own account binding. Only the excluded conflicting variants
        # must be absent; public results and diagnostics must stay public.
        public_and_diagnostics = json.dumps({
            "public": rows[:2],
            "rejections": vault.last_search_merge_rejections,
            "filtered_library": payload["library"],
        })
        for secret in (
            "PRIVATE_ALICE", "PRIVATE_BOB", "PRIVATE_TITLE", "PRIVATE_REVISION",
            "PRIVATE_TOKEN", "vault.example",
        ):
            assert secret not in public_and_diagnostics
        assert "PRIVATE_BOB" not in json.dumps(rows)
        assert "PRIVATE_TITLE_A" not in json.dumps(rows)
        assert "PRIVATE_TITLE_B" not in json.dumps(rows)
        assert "PRIVATE_REVISION" not in json.dumps(rows)
        assert "PRIVATE_ALIAS" not in json.dumps(payload)
    finally:
        vault.close()


def test_personal_duplicate_quarantine_resets_on_subsequent_search(monkeypatch):
    vault = KoboVaultClient("https://vault.example", "PRIVATE_TOKEN")
    state = {"duplicates": True}

    monkeypatch.setattr(
        vault, "search_catalog",
        lambda *_args, **_kwargs: {"items": [
            {"product_id": "shared", "title": "Catalogue"},
        ]},
    )

    def library(_query, **_kwargs):
        personal = {"product_id": "shared", "account_id": "current",
                    "owned": True, "downloadable": True}
        return {"items": [personal, dict(personal)] if state["duplicates"]
                else [personal]}

    monkeypatch.setattr(vault, "search_library", library)
    try:
        first = vault.search("query")["items"]
        assert len(first) == 1 and first[0]["owned"] is False
        assert len(vault.last_search_merge_rejections) == 2

        state["duplicates"] = False
        second = vault.search("query")["items"]
        assert len(second) == 1
        assert second[0]["owned"] is True
        assert second[0]["downloadable"] is True
        assert vault.last_search_merge_rejections == []
    finally:
        vault.close()
