"""P0 #4858 D3: a fresh Core admission must not retarget opaque legacy GET."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca import services
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_backup_guard import _manifest_row, _service


@pytest.mark.parametrize(("change", "value"), [
    ("source_provider", "numilog"),
    ("source_credential", "another-credential"),
    ("record_id", "another-edition"),
    ("revision_id", "another-revision"),
    ("owned", False),
    ("owned", None),
    ("downloadable", False),
])
def test_changed_fresh_admission_cannot_dispatch_other_edition(
    tmp_path: Path, monkeypatch, change, value,
):
    service = _service(tmp_path)
    item = service._sessions[
        service._session_key("kobo", "vault-1")
    ].normalize_item(_manifest_row())
    calls = []
    under_lock = []

    @contextmanager
    def lock(_path):
        under_lock.append(True)
        yield

    monkeypatch.setattr(services, "InterProcessLock", lock)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: calls.append("GET"),
    )
    try:
        with pytest.raises(
            ProviderConfigurationError, match="kobo_legacy_direct_admission_changed",
        ):
            service._acquire_direct_item_impl(
                item, _admission=lambda: replace(item, **{change: value}),
            )
        assert under_lock == [True]
        assert calls == []
        assert service.storage.backup_list("kobo", "vault-1") == []
        assert service.storage.acquisitions(provider_id="kobo") == []
        assert service.storage.placements() == []
    finally:
        service.close()


def test_mutating_original_selected_item_during_admission_cannot_retarget(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    item = service._sessions[
        service._session_key("kobo", "vault-1")
    ].normalize_item(_manifest_row())
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("mutable item must not retarget GET"),
    )
    try:
        def mutate():
            item.record_id = "other-book"
            return item

        with pytest.raises(
            ProviderConfigurationError, match="kobo_legacy_direct_admission_changed",
        ):
            service._acquire_direct_item_impl(item, _admission=mutate)
        assert service.storage.backup_list("kobo", "vault-1") == []
    finally:
        service.close()


def test_rebound_identity_after_admission_cannot_change_output_profile(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    item = service._sessions[
        service._session_key("kobo", "vault-1")
    ].normalize_item(_manifest_row())
    original = service.identity_for_connection
    calls = []
    def rebound(provider, credential):
        calls.append((provider, credential))
        return original(provider, credential) if len(calls) == 1 else SimpleNamespace(identity_id="bob")

    monkeypatch.setattr(service, "identity_for_connection", rebound)
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_a, **_kw: pytest.fail("foreign profile cannot receive GET"),
    )
    try:
        with pytest.raises(
            ProviderConfigurationError, match="kobo_legacy_direct_admission_changed",
        ):
            service._acquire_direct_item_impl(item, _admission=lambda: item)
        assert len(calls) == 2
    finally:
        service.close()


def test_same_edition_same_revision_fresh_admission_still_downloads(
    tmp_path: Path, monkeypatch,
):
    service = _service(tmp_path)
    item = service._sessions[
        service._session_key("kobo", "vault-1")
    ].normalize_item(_manifest_row())
    calls = []
    def acquire(selected, destination):
        calls.append((selected.record_id, selected.title, Path(destination)))
        return SimpleNamespace(path=tmp_path / "admitted.epub", metadata={})

    monkeypatch.setattr(service, "_execute_kobo_acquisition", acquire)
    try:
        received = service._acquire_direct_item_impl(
            item, _admission=lambda: replace(item, title="Fresh title"),
        )
        assert received.path == tmp_path / "admitted.epub"
        assert len(calls) == 1
        assert calls[0][0:2] == ("book-1", "Fresh title")
        assert "alice" in calls[0][2].parts
    finally:
        service.close()
