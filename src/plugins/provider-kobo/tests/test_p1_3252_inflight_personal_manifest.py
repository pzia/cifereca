"""#3252: Kobo manifest reads cannot complete under another session generation.

No HTTP, token requests, purchases, local file mutation or Core scheduler.
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace as NS

from cifereca.plugin_api import Account, ProviderError
from cifereca_kobo import DESCRIPTOR, KoboSession


class FakeVault:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0
        self.last_manifest_rejections = []

    def library_manifest(self):
        self.calls += 1
        return list(self.rows)

    def close(self):
        pass

    def acquisition(self, *_a, **_k):
        raise AssertionError("No acquisition for an inventory read")


def _session(vault):
    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="synthetic-card",
        label="Kobo", credentials={"api_token": "synthetic-token"},
        options={"base_url": "https://vault.example"},
        config={}, storage={}, diagnostics=NS(add=lambda *_a, **_kw: None),
        runtime_capabilities=NS(acquisition=lambda *_a: None),
    )
    session.client.close()
    session.client = vault
    session.catalog = NS(cover_url=lambda _row: None)
    session.connected = True
    session._accounts = [Account(
        unique_id="alice", display_name="Reader",
        provider_id="kobo", credential_id="synthetic-card",
    )]
    return session


@pytest.mark.parametrize("entrypoint", ["library", "backup_manifest"])
@pytest.mark.parametrize("race", ["disconnect", "same_account_reconnect", "account_replaced"])
def test_inflight_manifest_rejects_stale_account_or_epoch(entrypoint, race):
    vault = FakeVault([{
        "product_id": "synthetic-book", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    initial_account = session._accounts[0]
    initial_generation = session._personal_binding_generation
    original_fetch = vault.library_manifest

    def race_after_http():
        rows = original_fetch()
        if race == "disconnect":
            session.close()
        elif race == "same_account_reconnect":
            # Model a completed reconnect to the SAME account identity, an
            # ABA that ordinary account-ID comparisons cannot detect.
            session._personal_binding_generation += 1
        else:
            session._accounts = [Account(
                unique_id="alice", display_name="Reconnected account",
                provider_id="kobo", credential_id=session.credential_id,
            )]
        return rows

    vault.library_manifest = race_after_http
    if entrypoint == "library":
        session.normalize_item = lambda row, **kw: row
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            getattr(session, entrypoint)()
        assert vault.calls == 1
        assert session.last_library_rejections == []
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_rejection_count == 0
            assert session.backup_manifest_policy["manifest_authoritative"] is False
            assert session.backup_manifest_policy["acquisition_authorized"] is False
        if race == "same_account_reconnect":
            assert session.connected is True
            assert session._accounts[0] is initial_account
            assert session._personal_binding_generation == initial_generation + 1
    finally:
        session.close()


@pytest.mark.parametrize("entrypoint", ["library", "backup_manifest"])
def test_stable_personal_account_still_returns_exact_positive_manifest(entrypoint):
    vault = FakeVault([{
        "product_id": "synthetic-book", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    session.normalize_item = lambda row, **kw: row
    try:
        rows = getattr(session, entrypoint)()
        assert len(rows) == 1
        assert rows[0]["product_id"] == "synthetic-book"
        assert vault.calls == 1
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_policy["status"] == "classified"
    finally:
        session.close()


@pytest.mark.parametrize("race", ["disconnect", "same_account_reconnect", "account_replaced"])
@pytest.mark.parametrize("phase", ["library_normalize", "library_cover", "backup_cover"])
def test_manifest_epoch_is_rechecked_after_local_projection(phase, race):
    """A session changing AFTER Vault GET cannot publish prior account rights.

    The old first fence is deliberately allowed to pass: the race happens in
    the subsequent local projection, not in the HTTP client's response.
    """
    vault = FakeVault([{
        "product_id": "book-1", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    calls = []

    def change_binding():
        if not calls:
            calls.append("projection")
            if race == "disconnect":
                session.close()
            elif race == "same_account_reconnect":
                session._personal_binding_generation += 1
            else:
                session._accounts = [Account(
                    unique_id="alice", display_name="Rebound",
                    provider_id=session.provider_id,
                    credential_id=session.credential_id,
                )]

    def normalization(row, **kwargs):
        change_binding()
        return row

    def cover_url(row):
        change_binding()
        return None

    session.catalog.cover_url = cover_url if phase != "library_normalize" else lambda row: None
    session.normalize_item = normalization if phase == "library_normalize" else lambda row, **kwargs: row
    entrypoint = "backup_manifest" if phase == "backup_cover" else "library"
    try:
        with pytest.raises(ProviderError, match="session modifiée pendant lecture"):
            getattr(session, entrypoint)()
        assert calls == ["projection"]
        assert vault.calls == 1
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_rejection_count == 0
            assert session.backup_manifest_policy["manifest_authoritative"] is False
            assert session.backup_manifest_policy["acquisition_authorized"] is False
    finally:
        session.close()


@pytest.mark.parametrize("entrypoint", ["library", "backup_manifest"])
def test_valid_session_still_publishes_after_projection_fence(entrypoint):
    vault = FakeVault([{
        "product_id": "book-1", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    calls = []
    session.catalog.cover_url = lambda row: (calls.append("cover") or None)
    session.normalize_item = lambda row, **kwargs: (calls.append("normalize") or row)
    try:
        rows = getattr(session, entrypoint)()
        assert [row["product_id"] for row in rows] == ["book-1"]
        assert vault.calls == 1
        assert calls == (["cover", "normalize"] if entrypoint == "library" else ["cover"])
        if entrypoint == "backup_manifest":
            assert session.backup_manifest_policy["status"] == "classified"
    finally:
        session.close()


def test_backup_classification_is_not_published_midway_through_projection():
    vault = FakeVault([{
        "product_id": "book-1", "account_id": "alice",
        "owned": True, "downloadable": True,
    }])
    session = _session(vault)
    seen = []
    def observe_policy(row):
        seen.append(session.backup_manifest_policy["manifest_authoritative"])
        return None

    session.catalog.cover_url = observe_policy
    try:
        rows = session.backup_manifest()
        assert len(rows) == 1
        # No reader observing the post-HTTP cover/metadata phase can consume
        # a positive policy before its final generation fence has passed.
        assert seen == [False]
        assert session.backup_manifest_policy["manifest_authoritative"] is True
        assert vault.calls == 1
    finally:
        session.close()
