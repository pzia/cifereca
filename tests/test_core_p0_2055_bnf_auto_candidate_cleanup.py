"""P0 #2055: auto BnF token candidates abandoned before ADEPT are disposed.

Tests exercise real synthetic BnF HTTPX + Core, no live SSO or Adobe I/O.
"""
import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import materialize
from cifereca.plugin_api import Cancelled
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def admitted(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    return service, session, account, loan, broker, downloaded


def test_revocation_after_token_get_discards_only_new_private_acsm(
    connected_core, monkeypatch,
):
    service, session, account, loan, broker, downloaded = admitted(connected_core)
    staged = []
    imported = []
    original = session.download_digital
    monkeypatch.setattr(service, "import_acsm", lambda *a, **kw: imported.append(kw))

    def revoke_after_get(*args, **kw):
        candidate, url = original(*args, **kw)
        staged.append(candidate)
        broker.set_bnf_auto_download(broker._source_id(account), False)
        return candidate, url

    monkeypatch.setattr(session, "download_digital", revoke_after_get)
    result = materialize(service, account, loan)
    assert result == {"status": "attention_required", "code": "bnf_loan_scope_unproven"}
    assert len(staged) == len(downloaded) == 1 and imported == []
    assert not staged[0].exists()
    assert service.storage.acquisitions(provider_id="manual") == []


def test_corrupted_candidate_is_removed_before_any_adept_import(
    connected_core, monkeypatch,
):
    service, session, account, loan, _broker, downloaded = admitted(connected_core)
    original = session.download_digital
    staged = []
    imported = []
    monkeypatch.setattr(service, "import_acsm", lambda *a, **kw: imported.append(kw))

    def corrupt_after_get(*args, **kw):
        candidate, url = original(*args, **kw)
        staged.append(candidate)
        candidate.write_bytes(b"<html>synthetic invalid token</html>")
        return candidate, url

    monkeypatch.setattr(session, "download_digital", corrupt_after_get)
    result = materialize(service, account, loan)
    assert result == {"status": "attention_required", "code": "bnf_loan_token_invalid"}
    assert len(staged) == len(downloaded) == 1 and imported == []
    assert not staged[0].exists()
    assert service.storage.acquisitions(provider_id="manual") == []


def test_cancel_after_get_removes_candidate_but_keeps_task_cancelled(
    connected_core, monkeypatch,
):
    service, session, account, loan, _broker, downloaded = admitted(connected_core)
    original = session.download_digital
    staged = []
    imported = []
    monkeypatch.setattr(service, "import_acsm", lambda *a, **kw: imported.append(kw))

    def capture(*args, **kw):
        candidate, url = original(*args, **kw)
        staged.append(candidate)
        return candidate, url

    class Control:
        calls = 0

        def check_cancelled(self):
            self.calls += 1
            if self.calls == 2:
                raise Cancelled("synthetic cancelled")

    monkeypatch.setattr(session, "download_digital", capture)
    control = Control()
    with pytest.raises(Cancelled):
        materialize(service, account, loan, control=control)
    assert control.calls == 2
    assert len(downloaded) == len(staged) == 1 and imported == []
    assert not staged[0].exists()
    assert service.storage.acquisitions(provider_id="manual") == []


def test_untrusted_external_acsm_is_never_deleted(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, _broker, downloaded = admitted(connected_core)
    outside = tmp_path / "foreign-private.acsm"
    outside.write_bytes(b"synthetic outside marker")
    before = outside.read_bytes()
    imports = []
    monkeypatch.setattr(service, "import_acsm", lambda *a, **kw: imports.append(kw))
    monkeypatch.setattr(session, "download_digital", lambda *args, **kw: (outside, ""))
    result = materialize(service, account, loan)
    assert result == {"status": "attention_required", "code": "bnf_loan_token_invalid"}
    assert outside.read_bytes() == before
    assert imports == downloaded == []


def test_late_pre_import_failure_cannot_remove_other_paths(
    connected_core, tmp_path, monkeypatch,
):
    service, session, account, loan, _broker, downloaded = admitted(connected_core)
    outside = tmp_path / "foreign-private.acsm"
    outside.write_bytes(b"external marker")
    original = session.download_digital
    staged = []
    imports = []
    monkeypatch.setattr(service, "import_acsm", lambda *a, **kw: imports.append(kw))

    def return_bad_url(*args, **kw):
        candidate, _url = original(*args, **kw)
        staged.append(candidate)
        return candidate, "https://private.invalid/path"

    monkeypatch.setattr(session, "download_digital", return_bad_url)
    result = materialize(service, account, loan)
    assert result == {"status": "attention_required", "code": "bnf_loan_token_invalid"}
    assert len(staged) == len(downloaded) == 1 and imports == []
    assert not staged[0].exists() and outside.read_bytes() == b"external marker"
