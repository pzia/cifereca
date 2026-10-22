"""Launch the full installed Cifereca executable twice under Xvfb.

Isolated XDG data/config/cache/state and DBus session; fictional encrypted
sessions only. The OS/X11 window is closed by WM_DELETE_WINDOW, not via a
mocked GTK/PluginManager constructor. No browser, account, screenshot, HAR,
private export or network request is needed to reopen the session vault.
"""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic, sleep

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_SOURCE = ROOT / "src" / "plugins" / "browser-recorder"
PLUGIN_ID = "ui.page.browser-recorder"
PASSWORD = "FICTIONAL-RESTART-TEST-PASSWORD-2026"
PRIVATE_SENTINEL = "FICTIONAL_ACCOUNT_NEVER_LOG_E2E_256"
ORIGIN = "https://fixture-vault-restart.example.org"


def test_full_executable_restart_contract_visible_without_native_gtk():
    """Still collectable by generic Core PR CI without GTK or Playwright."""
    manifest = json.loads(
        (PLUGIN_SOURCE / "cifereca-plugin.json").read_text("utf-8")
    )
    assert manifest["plugins"][0]["id"] == PLUGIN_ID
    assert manifest["plugins"][0]["type"] == "ui.page"
    assert manifest["plugins"][0]["enabled_by_default"] is False


@pytest.mark.skipif(
    shutil.which("xdotool") is None or shutil.which("dbus-run-session") is None,
    reason="full application window oracle requires Xvfb/X11 and DBus session",
)
def test_full_executable_terminate_and_restart_with_three_private_sessions(
    tmp_path, monkeypatch, request,
):
    pytest.importorskip("cryptography")
    from cifereca.core import CiferecaCore
    from cifereca.paths import data_dir
    from scripts.plugin_packaging import build_plugin_zip

    # Running these real sessions is already covered by the separate three
    # headed Chromium E2E. This test isolates *full app boot + X11 close*.
    for variable, folder in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_DATA_HOME", "data"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_STATE_HOME", "state"),
    ):
        root = tmp_path / folder
        root.mkdir(mode=0o700)
        monkeypatch.setenv(variable, str(root))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GDK_BACKEND", "x11")
    monkeypatch.setenv("GSETTINGS_BACKEND", "memory")
    env = dict(os.environ)
    vault_dir = data_dir() / "plugins" / "data" / PLUGIN_ID / "private-sessions"
    packager_output = tmp_path / "build"
    packager_output.mkdir(mode=0o700)
    package_zip = build_plugin_zip(PLUGIN_SOURCE, packager_output)
    assert package_zip.exists()
    with CiferecaCore.open(activate_plugins=False) as core:
        core.plugins.installer.install(package_zip, activate=True)
        core.plugins.activate()
        assert core.plugins.service(PLUGIN_ID) is None
        core.plugins.set_enabled(PLUGIN_ID, True)
        assert core.plugins.status(PLUGIN_ID).state == "ready"
        assert core.plugins.service(PLUGIN_ID) is not None
        # The full-app restart recipe must follow the package being built,
        # not a previous hard-coded Browser Recorder release.
        release = json.loads(
            (PLUGIN_SOURCE / "cifereca-plugin.json").read_text("utf-8")
        )["package"]
        installed_root = core.plugins.packages.version_root(
            release["id"], release["version"],
        )
        assert installed_root.is_dir()
    # Only the *installed* plugin's code supplies fixture serializers; do not
    # put the checked-out plugin source on the child's import path.
    sys.path.insert(0, str(installed_root))
    from browser_recorder_http import PrivateExchange, PrivateHttpTrace
    from browser_recorder_vault import PrivateSessionVault
    vault = PrivateSessionVault(vault_dir)
    originals = {}
    for n in range(3):
        writer = vault.begin(PASSWORD)
        trace = PrivateHttpTrace((ORIGIN,))
        trace.exchanges.append(PrivateExchange(
            1, "GET", ORIGIN + "/catalog?n=" + str(n)
            + "&account=" + PRIVATE_SENTINEL,
            status=200, mime="application/json",
            response_body=json.dumps({
                "ProductId": 200 + n, "account": PRIVATE_SENTINEL,
            }), response_body_state="captured",
        ))
        trace.exchanges.append(PrivateExchange(
            2, "POST", ORIGIN + "/detail", status=302,
            request_body=json.dumps({"account": PRIVATE_SENTINEL}),
            request_body_state="captured",
        ))
        writer.save(trace, "completed", force=True)
        trace.destroy()
        originals[writer.session_id] = sha256(
            (vault_dir / (writer.session_id + ".cbrv")).read_bytes()
        ).hexdigest()
    assert len(originals) == 3
    assert set(vault.list_ids()) == set(originals)

    # New OS interpreter, full Cifereca startup, Adw.Application, real Core
    # storage open, real MainWindow, plugin startup from installed package.
    executable = Path(sys.executable).parent / "cifereca"
    assert executable.exists(), "the isolated test venv must install Cifereca"
    # Real X11 window manager: Xvfb alone lacks normal Alt+F4 semantics.
    assert shutil.which("openbox"), "Openbox required for X11 smoke recipe"
    wm = subprocess.Popen(
        ["openbox", "--sm-disable"], env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    request.addfinalizer(lambda: wm.terminate() if wm.poll() is None else None)
    sleep(.65)
    assert wm.poll() is None, "window manager failed to start"
    launches = []
    for turn in (1, 2):
        app_log = tmp_path / f"app-launch-{turn}.txt"
        with app_log.open("wb") as sink:
            app = subprocess.Popen(
                ["dbus-run-session", "--", str(executable), "--verbose"],
                cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                stdout=sink, stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                window_id = None
                stop_at = monotonic() + 35
                while monotonic() < stop_at:
                    assert app.poll() is None, "full Cifereca executable crashed"
                    found = subprocess.run(
                        ["xdotool", "search", "--onlyvisible", "--name",
                         "cifereca"],
                        env=env, capture_output=True, timeout=3,
                    )
                    if found.returncode == 0 and found.stdout.strip():
                        window_id = found.stdout.splitlines()[0].decode("ascii")
                        break
                    sleep(.12)
                assert window_id is not None, "no real X11 Cifereca window"
                # Allow the deferred Core plugin activation idle callback.
                sleep(1.0)
                assert app.poll() is None
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", window_id],
                    env=env, capture_output=True, check=True, timeout=5,
                )
                subprocess.run(
                    ["xdotool", "key", "--clearmodifiers", "alt+F4"],
                    env=env, capture_output=True, check=True, timeout=5,
                )
                rc = app.wait(timeout=18)
                if rc != 0:
                    # Emit *only known safe diagnostic codes*. Never dump raw
                    # logs that may someday contain real account URLs.
                    content = app_log.read_text(encoding="utf-8", errors="replace")
                    probes = {
                        "startup": "TRACE startup begin" in content,
                        "shown": "TRACE gui window-presented" in content,
                        "activated": "TRACE gui plugin-activation begin" in content,
                        "shutdown": "TRACE shutdown app.run end" in content,
                        "fatal": "Fatal GTK GUI error" in content,
                        "traceback": "Traceback" in content,
                        "critical": "CRITICAL" in content,
                    }
                    hints = []
                    for line in content.splitlines()[-100:]:
                        if any(word in line for word in (
                            "Error", "ERROR", "Exception", "exception",
                            "CRITICAL", "Traceback", "shutdown", "WARNING",
                            "dbus-run-session",
                        )):
                            for secret in (PRIVATE_SENTINEL, PASSWORD, ORIGIN,
                                           str(tmp_path)):
                                line = line.replace(secret, "<REDACTED>")
                            hints.append(line[-260:])
                    pytest.fail(
                        f"full application exit={rc}; probes={probes}; "
                        f"redacted_hints={hints[-10:]}"
                    )
            finally:
                if app.poll() is None:
                    app.terminate()
                    try:
                        app.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        app.kill()
                        app.wait(timeout=6)
        logs = app_log.read_bytes()
        # Fake captured credentials, titles and origin must never be logged,
        # even when verbose startup and plugin activation are enabled.
        for private in (PRIVATE_SENTINEL, PASSWORD, ORIGIN):
            assert private.encode() not in logs
        assert b"TRACE startup" in logs
        assert b"TRACE gui window-presented" in logs
        assert b"TRACE gui plugin-activation begin" in logs
        assert b"TRACE shutdown app.run end" in logs
        assert b"Fatal GTK GUI error" not in logs
        launches.append(logs)
        assert set(vault.list_ids()) == set(originals)
        assert {
            sid: sha256((vault_dir / (sid + ".cbrv")).read_bytes()).hexdigest()
            for sid in originals
        } == originals
        for sid in originals:
            item = vault.open(sid, PASSWORD)
            assert item.recovery_status == "completed"
            assert len(item.trace.exchanges) == 2
            item.trace.destroy()
    assert len(launches) == 2
    assert not list(vault_dir.glob("*.zip"))
    assert not list(vault_dir.glob("*.har"))
    assert not list(vault_dir.glob("*.part"))
