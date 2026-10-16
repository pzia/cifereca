"""#1905: a targeted acquisition admission must never be silently ignored."""
from __future__ import annotations

from dataclasses import replace

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_sync_state_contract import _service


@pytest.mark.parametrize("provider", ["numilog", "paris", "third-party"])
def test_explicit_admission_non_kobo_rejected_before_any_session_or_download(
    tmp_path, monkeypatch, provider,
):
    service = _service(tmp_path)
    try:
        account, session, provider_calls = _connect(service, number=1)
        item = replace(
            _row(account.credential_id, 0),
            source_provider=provider,
        )
        attempted = []
        def provider_access(*_args, **_kwargs):
            attempted.append("provider")
            pytest.fail("no provider lookup before admission")
        def callback():
            attempted.append("admission")
            pytest.fail("unsupported admission must fail closed")
        monkeypatch.setattr(service, "_session_for_source", provider_access)
        monkeypatch.setattr(session, "download_direct", provider_access)
        with pytest.raises(
            ProviderConfigurationError, match="autorisation du compte non vérifiable",
        ):
            service._acquire_direct_item_impl(item, _admission=callback)
        assert attempted == []
        assert provider_calls == []
        assert not list((tmp_path / "EBooks").rglob("*.epub"))
        assert not list((tmp_path / "EBooks").rglob("*.acsm"))
    finally:
        service.close()


def test_non_kobo_targeted_admission_rejects_bad_truthy_and_noncallable_payloads(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        account, session, calls = _connect(service, number=0)
        item = _row(account.credential_id, 1)
        monkeypatch.setattr(
            session, "download_direct",
            lambda *_args: pytest.fail("noncallable admission bypassed"),
        )
        for admission in (True, False, "enabled", {}, object()):
            with pytest.raises(ProviderConfigurationError):
                service._acquire_direct_item_impl(item, _admission=admission)
        assert calls == []
    finally:
        service.close()


def test_unscoped_legacy_numilog_download_api_not_switched_off_by_guard(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    try:
        account, session, _calls = _connect(service, number=0)
        item = _row(account.credential_id, 2)
        reached = []
        def expected_fictitious_provider_call(*_args):
            reached.append("explicit_direct")
            raise RuntimeError("fictional download callback, no external request")
        monkeypatch.setattr(session, "download_direct", expected_fictitious_provider_call)
        # The P0 guard does not widen or disable the existing explicitly
        # requested provider API. This test invokes only a local fake callback.
        with pytest.raises(RuntimeError, match="fictional download"):
            service._acquire_direct_item_impl(item)
        assert reached == ["explicit_direct"]
    finally:
        service.close()
