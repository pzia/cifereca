"""#8438: opening worker preserves closed lifecycle signals without private text."""
from types import SimpleNamespace as NS

import pytest

from cifereca.core.sync_coordination import opening_sync
from cifereca.locking import InterProcessLock, LockBusyError
from cifereca.plugin_api import Cancelled
from cifereca.providers import ProviderConfigurationError
from tests.test_core_p0_1346_kobo_on_start_inventory_gui import _startup_window


def test_opening_worker_reports_sanitized_real_contention(tmp_path):
    path = tmp_path / "PRIVATE_SENTINEL.lock"
    core = NS(_opening_sync_locked=lambda **kw: pytest.fail("entered locked work"))
    r, _, run_opening = _startup_window()
    r.window.service.opening_sync = lambda **kw: opening_sync(
        core, lock_factory=InterProcessLock, lock_path=lambda: path, **kw
    )
    with InterProcessLock(path):
        run_opening(r.window)
        with pytest.raises(LockBusyError) as caught:
            r.jobs.pop().work(lambda *_: None)
    assert "PRIVATE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("error,expected", [
    (Cancelled("PRIVATE_SENTINEL"), Cancelled),
    (ProviderConfigurationError("PRIVATE_SENTINEL"), RuntimeError),
])
def test_opening_worker_redacts_cancel_and_provider_failure(error, expected):
    r, _, run_opening = _startup_window()
    def fail(**kw):
        raise error
    r.window.service.opening_sync = fail
    run_opening(r.window)
    with pytest.raises(expected) as caught:
        r.jobs.pop().work(lambda *_: None)
    assert type(caught.value) is expected
    assert "PRIVATE_SENTINEL" not in str(caught.value)
    assert caught.value.__cause__ is None
