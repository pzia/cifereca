"""P1 #1346: Kobo provider diagnostics must not expose connection provenance.

Synthetic Vault results only; no account, API, acquisition or DRM requests.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace as NS

from cifereca.plugin_api import Account
from cifereca_kobo import DESCRIPTOR, KoboSession


class FakeVault:
    last_manifest_rejections = ()

    def __init__(self, items):
        self.items = items
        self.calls = 0

    def library_manifest(self):
        self.calls += 1
        return self.items

    def close(self):
        pass


def test_search_library_and_backup_traces_contain_counters_only(caplog):
    """Never print credential, untrusted JSON keys, URLs, titles or tokens."""
    private = {
        "product_id": "PRIVATE_BOOK_ID",
        "title": "PRIVATE_BOOK_TITLE",
        "cover_url": None,
        "signed_url": "https://PRIVATE_HOST.invalid/?token=PRIVATE_TOKEN",
        "PRIVATE_KEY_BEARER_SECRET": "PRIVATE_VALUE",
        "account_id": "PRIVATE_ACCOUNT",
    }
    session = KoboSession(
        descriptor=DESCRIPTOR,
        credential_id="PRIVATE_CREDENTIAL", label="Private reader",
        credentials={"api_token": "PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=NS(add=lambda *a, **k: None),
        runtime_capabilities=NS(acquisition=lambda *_: None),
    )
    session.client.close()
    vault = FakeVault([private, {
        "product_id": "PUBLIC_BOOK", "cover_url": "https://example.com/a.jpg",
    }])
    session.client = vault
    session.connected = True
    session._accounts = [Account(
        unique_id="PRIVATE_ACCOUNT", provider_id="kobo",
        credential_id=session.credential_id, display_name="Private reader",
    )]
    session.catalog = NS(
        cover_url=lambda item: item.get("cover_url"),
        normalize_item=lambda *_args, **_kw: NS(title="private"),
        search=lambda *a, **kw: [NS(thumbnail_url=None), NS(thumbnail_url="cover")],
    )
    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            assert len(session.search("PRIVATE_QUERY")) == 2
            assert len(session.library()) == 2
            assert len(session.backup_manifest()) == 2

        traces = [
            record.getMessage()
            for record in caplog.records
            if record.getMessage().startswith("TRACE kobo ")
        ]
        assert len(traces) == 3
        assert traces[0] == "TRACE kobo catalog plugin source=search items=2 with_cover=1"
        assert traces[1] == "TRACE kobo covers source=library items=2 with_cover=1 missing=1"
        assert traces[2] == "TRACE kobo covers source=backup items=2 with_cover=1 missing=1"
        assert vault.calls == 2
        for secret in (
            "PRIVATE", "credential=", "sample_missing_keys", "signed_url",
            "Bearer", "vault.example", "product_id", "account_id",
            "cover_url", "https://",
        ):
            assert all(secret not in trace for trace in traces)
    finally:
        session.close()


def test_debug_traces_do_not_iterate_or_reveal_untrusted_manifest_keys(caplog):
    """Field names returned by Vault may themselves be secrets."""
    class PoisonMapping(dict):
        def keys(self):
            raise AssertionError("A diagnostic must never enumerate untrusted keys")

    source = PoisonMapping({
        "product_id": "PRIVATE_BOOK",
        "PRIVATE_BEARER_TOKEN_AS_KEY": "secret",
    })
    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="PRIVATE_ACCOUNT",
        label="Kobo", credentials={"api_token": "PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=NS(add=lambda *a, **k: None),
        runtime_capabilities=NS(acquisition=lambda *_: None),
    )
    session.client.close()
    session.client = FakeVault([source])
    session.connected = True
    session._accounts = [Account(
        unique_id="PRIVATE_ACCOUNT", provider_id="kobo",
        credential_id=session.credential_id, display_name="Private reader",
    )]
    session.catalog = NS(cover_url=lambda _item: None)
    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            assert len(session.backup_manifest()) == 1
        trace = " ".join(
            rec.getMessage() for rec in caplog.records
            if rec.getMessage().startswith("TRACE kobo covers")
        )
        assert "items=1" in trace and "missing=1" in trace
        assert "PRIVATE" not in trace
    finally:
        session.close()
