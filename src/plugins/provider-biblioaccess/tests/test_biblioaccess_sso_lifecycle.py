"""Synthetic lifecycle contract: no real browser, authentication or secrets."""
import importlib.util
from pathlib import Path
import sys

import pytest

PATH = Path(__file__).resolve().parents[1] / "biblioaccess_sso_lifecycle.py"
spec = importlib.util.spec_from_file_location("biblioaccess_sso_lifecycle", PATH)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


def scope(identity="id-one", credential="cred-one"):
    return mod.SsoScope(identity_id=identity, credential_id=credential)


def test_consent_and_observation_never_automatically_connect_core():
    life = mod.InteractiveSsoLifecycle(scope())
    marker = object()
    with pytest.raises(ValueError, match="explicit_consent"):
        life.begin(consent=False, browser_marker=marker, now=100)
    nonce = life.begin(consent=True, browser_marker=marker, now=100)
    assert len(nonce) == 32
    assert not life.live_browser(nonce=nonce, browser_marker=marker, scope=scope(), now=101)
    assert life.confirm_browser_observation(
        nonce=nonce, browser_marker=marker, scope=scope(),
        page_is_authenticated=True, now=101,
    ) == "browser_confirmed"
    assert life.live_browser(nonce=nonce, browser_marker=marker, scope=scope(), now=102)
    assert life.diagnostic_state()["core_session_connected"] is False
    assert nonce not in repr(life)


def test_no_cross_identity_credential_or_browser():
    s = scope()
    life = mod.InteractiveSsoLifecycle(s)
    marker = object()
    nonce = life.begin(consent=True, browser_marker=marker, now=0)
    for alternate, foreign_marker in (
        (scope("id-two"), marker), (scope("id-one", "cred-two"), marker),
        (scope(), object()),
    ):
        assert life.confirm_browser_observation(
            nonce=nonce, browser_marker=foreign_marker,
            scope=alternate, page_is_authenticated=True, now=1,
        ) == "rejected"
    assert life.confirm_browser_observation(
        nonce="SYNTHETIC-BAD", browser_marker=marker,
        scope=s, page_is_authenticated=True, now=1,
    ) == "rejected"
    assert life.phase == "pending"


def test_false_positive_browser_200_not_enough():
    life = mod.InteractiveSsoLifecycle(scope())
    marker = object()
    nonce = life.begin(consent=True, browser_marker=marker, now=0)
    assert life.confirm_browser_observation(
        nonce=nonce, browser_marker=marker, scope=scope(),
        page_is_authenticated=False, now=1,
    ) == "rejected"
    assert life.phase == "pending"


def test_expiration_and_replay_deny():
    life = mod.InteractiveSsoLifecycle(scope())
    marker = object()
    nonce = life.begin(consent=True, browser_marker=marker, now=0)
    assert life.confirm_browser_observation(
        nonce=nonce, browser_marker=marker, scope=scope(),
        page_is_authenticated=True, now=901,
    ) == "expired"
    assert not life.live_browser(nonce=nonce, browser_marker=marker, scope=scope(), now=902)
    fresh = life.begin(consent=True, browser_marker=object(), now=1000)
    assert fresh != nonce
    assert life.confirm_browser_observation(
        nonce=nonce, browser_marker=marker, scope=scope(),
        page_is_authenticated=True, now=1001,
    ) == "rejected"


def test_cancel_close_and_late_callbacks():
    life = mod.InteractiveSsoLifecycle(scope())
    marker = object()
    nonce = life.begin(consent=True, browser_marker=marker, now=0)
    life.invalidate("cancelled")
    assert life.confirm_browser_observation(
        nonce=nonce, browser_marker=marker, scope=scope(),
        page_is_authenticated=True, now=1,
    ) == "rejected"
    life.invalidate("closed")
    assert not life.live_browser(nonce=nonce, browser_marker=marker, scope=scope(), now=1)
    with pytest.raises(ValueError):
        life.begin(consent=True, browser_marker=marker, now=2)


def test_foreign_tenant_and_invalid_scope():
    with pytest.raises(ValueError, match="foreign_tenant"):
        mod.SsoScope(identity_id="i", credential_id="c", tenant_id="1717")
    with pytest.raises(ValueError, match="invalid_scope"):
        mod.SsoScope(identity_id="", credential_id="c")
