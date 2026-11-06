"""#4673: Paris search prefers the explicitly configured primary account.

No real Paris I/O or loan POST. Both search paths share the same selection,
cache partition and in-flight primary-change revocation contract.
"""
import pytest

from tests.test_core043_progress_cache_stats import _FakeSession, _service


def _fixture(tmp_path, monkeypatch, *, reverse=False):
    service = _service(tmp_path)
    other = _FakeSession("paris")
    other.credential_id = "paris-other-reader"
    secondary = _FakeSession("paris")
    secondary.credential_id = "paris-secondary"
    primary = _FakeSession("paris")
    primary.credential_id = "paris-primary"
    sessions = [("other", other), ("secondary", secondary), ("primary", primary)]
    service._sessions = dict(reversed(sessions) if reverse else sessions)
    monkeypatch.setattr(
        service.storage, "identity_for_connection",
        lambda provider, credential: {
            ("paris", "paris-other-reader"): "bob",
            ("paris", "paris-secondary"): "alice",
            ("paris", "paris-primary"): "alice",
        }.get((provider, credential)),
    )
    monkeypatch.setattr(service, "_mirror_remote_results", lambda *a, **kw: 0)
    _select_primary(service, "paris-primary")
    return service, primary, secondary


def _select_primary(service, credential):
    saved = {"id": "alice", "label": "Alice"}
    if credential is not None:
        saved["primary_paris_connection_id"] = credential
    service.config.set("identities", [saved])


def _search(service, progressive, *, force=False, on_batch=None):
    if progressive:
        result = service.search_progressive(
            "Serpentine", provider_id="paris", force=force, on_batch=on_batch,
        )
        return result, service.last_search
    return None, service.search(
        "Serpentine", provider_id="paris", force=force,
    )


@pytest.mark.parametrize("progressive", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_explicit_primary_outweighs_other_owned_credential_and_connection_order(
    tmp_path, monkeypatch, progressive, reverse,
):
    service, primary, secondary = _fixture(tmp_path, monkeypatch, reverse=reverse)
    try:
        _, fresh = _search(service, progressive, force=True)
        assert len(fresh) == 1
        assert fresh[0].source_credential == "paris-primary"
        _, cached = _search(service, progressive)
        assert len(cached) == 1
        assert cached[0].source_credential == "paris-primary"

        # Same profile, but an explicit Settings change, with no reconnect.
        # The different selected credential must never reuse the old cache.
        _select_primary(service, "paris-secondary")
        _, changed = _search(service, progressive)
        assert len(changed) == 1
        assert changed[0].source_credential == "paris-secondary"
        _select_primary(service, "paris-primary")
        _, again = _search(service, progressive)
        assert len(again) == 1
        assert again[0].source_credential == "paris-primary"
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_missing_primary_keeps_catalogue_provenance_but_never_rebinds(
    tmp_path, monkeypatch, progressive,
):
    service, primary, secondary = _fixture(tmp_path, monkeypatch)
    try:
        primary.connected = False
        _, rows = _search(service, progressive, force=True)
        assert len(rows) == 1
        assert rows[0].source_credential == "paris-secondary"
        # The normal Paris loan guard must not authorize this secondary
        # credential as the explicitly selected primary.
        from cifereca.core.manual_paris_borrow import _normal_paris_source_current
        assert not _normal_paris_source_current(service, rows[0], "alice")
    finally:
        service.close()


@pytest.mark.parametrize("progressive", [False, True])
def test_changing_primary_during_provider_search_revokes_old_result(
    tmp_path, monkeypatch, progressive,
):
    service, primary, secondary = _fixture(tmp_path, monkeypatch)
    published = []
    try:
        original = primary.search

        def change_primary(query, scope, **kwargs):
            _select_primary(service, "paris-secondary")
            return original(query, scope, **kwargs)

        primary.search = change_primary
        summary, rows = _search(
            service, progressive, force=True, on_batch=published.append,
        )
        if progressive:
            assert summary.cancelled is True
            assert published == []
        assert rows == []
        assert service.last_search == []
    finally:
        service.close()
