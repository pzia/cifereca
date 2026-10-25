"""Offline process-boundary proofs for #925; no real updater mutations/network."""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cifereca import dev_updater, updater, update_child
from cifereca.update_progress import child_progress_receiver, update_operation, update_phase

SECRET = "https://private.invalid/?token=CHILD_SECRET"


def _run(tmp_path, code, *, on_line=lambda _line: None, timeout=5):
    script = tmp_path / "child.py"
    script.write_text(textwrap.dedent(code))
    return update_child.run_plugin_child([sys.executable, str(script)],
                                         timeout=timeout, on_line=on_line)


def _line(**fields):
    return json.dumps(dict(schema="cifereca.update.progress/1",
                           stage="plugin_install", status="RUNNING", **fields)).encode()


def test_event_is_received_before_child_can_finish(tmp_path):
    marker = tmp_path / "ack"
    events, stream = [], io.StringIO()
    with update_operation("archive", callback=events.append, stream=stream):
        receive = child_progress_receiver()
        def acknowledge(line):
            receive(line)
            marker.touch()
        result = _run(tmp_path, f'''
            import json, os, sys, time
            from pathlib import Path
            assert os.environ["CIFERECA_UPDATE_PROGRESS_JSONL"] == "1"
            print(json.dumps({{"schema":"cifereca.update.progress/1", "stage":"plugin_install",
                              "status":"RUNNING", "plugin_index":2, "plugin_total":3,
                              "transaction_ref":"{SECRET}", "method":"{SECRET}",
                              "timestamp":"{SECRET}", "extra":"{SECRET}"}}), file=sys.stderr, flush=True)
            deadline = time.monotonic() + 3
            while not Path({str(marker)!r}).exists():
                assert time.monotonic() < deadline, "parent buffered progress"
                time.sleep(.01)
            print('{{"ok":true}}', flush=True)
        ''', on_line=acknowledge)
    assert json.loads(result.stdout) == {"ok": True}
    ordinal = [e for e in events if e["stage"] == "plugin_install"]
    assert [(e["status"], e["plugin_index"], e["plugin_total"]) for e in ordinal] == [("RUNNING", 2, 3)]
    assert len({e["transaction_ref"] for e in events}) == 1
    assert all(e["method"] == "archive" for e in events)
    assert SECRET not in repr(events) + stream.getvalue()


def test_both_pipes_drain_and_oversized_lines_resynchronize(tmp_path):
    events = []
    with update_operation("source", callback=events.append):
        result = _run(tmp_path, '''
            import json, sys
            sys.stdout.write(' ' * 200000)
            sys.stdout.flush()
            sys.stderr.write('sensitive' * 100000 + '\\n')
            sys.stderr.write('not-json\\n')
            sys.stderr.write(json.dumps({"schema":"cifereca.update.progress/1", "stage":"plugin_install",
                "status":"OK", "plugin_index":1,"plugin_total":1}) + '\\n')
            sys.stderr.flush()
            print('{"ok":true}')
        ''', on_line=child_progress_receiver())
    assert json.loads(result.stdout)["ok"] is True
    assert [(e["status"], e.get("plugin_index")) for e in events
            if e["stage"] == "plugin_install"] == [("OK", 1)]
    assert "sensitive" not in repr(events)


def test_stdout_limit_fails_without_retaining_raw_output(tmp_path, monkeypatch):
    monkeypatch.setattr(update_child, "MAX_STDOUT_BYTES", 1024)
    with pytest.raises(update_child.ChildOutputError) as failure:
        _run(tmp_path, "import sys; sys.stdout.write('CHILD_SECRET' * 10000)")
    assert "CHILD_SECRET" not in str(failure.value)


def test_record_and_event_count_limits_bound_the_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(update_child, "MAX_EVENTS", 2)
    lines = []
    _run(tmp_path, '''
        import sys
        sys.stderr.write('x' * 2048 + '\\n' + 'y' * 2049 + '\\n' + 'z\\n' + 'extra\\n')
        sys.stderr.flush()
        print('{}')
    ''', on_line=lines.append)
    assert lines == [b"x" * 2048, b"z"]


@pytest.mark.parametrize("record", [b"not-json", b"\xff", b"[]", b"null",
    b'{"schema":"unknown"}', b"[" * 1500,
    _line(plugin_index=True, plugin_total=3),
    _line(plugin_index=1, plugin_total=2049),
    json.dumps({"schema":"cifereca.update.progress/1","stage":[],"status":{}}).encode(),
    json.dumps({"schema":"cifereca.update.progress/1","stage":"operation","status":"DONE",
                "outcome":"success"}).encode(),
    json.dumps({"schema":"cifereca.update.progress/1","stage":"plugin_install","status":"DONE"}).encode(),
])
def test_hostile_fields_never_inject_parent_metadata_or_completion(record):
    events = []
    with update_operation("source", callback=events.append):
        child_progress_receiver()(record)
    assert not any(e.get("plugin_index") is not None for e in events)
    assert sum(e["status"] == "DONE" for e in events) == 1
    assert events[-1]["stage"] == "operation"


def test_error_reason_and_duration_are_strictly_allowlisted():
    events = []
    with update_operation("source", callback=events.append):
        receive = child_progress_receiver()
        for reason, duration in [([], []), (SECRET, -1), ("timeout", 42)]:
            receive(json.dumps({"schema":"cifereca.update.progress/1", "stage":"smoke",
                               "status":"ERROR", "reason_code":reason,
                               "duration_ms":duration}).encode())
    phases = [e for e in events if e["stage"] == "smoke"]
    assert [e.get("reason_code") for e in phases] == [None, None, "timeout"]
    assert phases[-1]["duration_ms"] >= 42
    assert SECRET not in repr(events)


def test_timeout_reaps_child_before_returning_to_rollback(tmp_path, monkeypatch):
    children = []
    real_popen = subprocess.Popen
    def track(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(update_child.subprocess, "Popen", track)
    with pytest.raises(TimeoutError):
        _run(tmp_path, "import time; time.sleep(10)", timeout=.2)
    assert len(children) == 1
    assert children[0].poll() is not None


def test_orphan_pip_descendant_is_killed_before_rollback(tmp_path, monkeypatch):
    """The CLI can exit while pip still owns stdout/stderr; reap the group."""
    if os.name != "posix":
        pytest.skip("POSIX process groups required")
    pid_file = tmp_path / "descendant-pid"
    real_killpg = update_child.os.killpg
    killed = []

    def record_killpg(pgid, signum):
        killed.append((pgid, signum))
        return real_killpg(pgid, signum)

    monkeypatch.setattr(update_child.os, "killpg", record_killpg)
    with pytest.raises(TimeoutError):
        _run(tmp_path, f'''
            import subprocess, sys
            from pathlib import Path
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdout=sys.stdout, stderr=sys.stderr,
            )
            Path({str(pid_file)!r}).write_text(str(child.pid))
            print('{{"ok":true}}', flush=True)
        ''', timeout=1.5)
    assert pid_file.is_file()
    assert killed and killed[-1][1] == update_child.signal.SIGKILL


def test_interrupt_reaps_child_and_no_more_events_escape(tmp_path):
    pid = tmp_path / "pid"
    seen = []
    def interrupt(line):
        seen.append(line)
        raise KeyboardInterrupt(SECRET)
    with pytest.raises(KeyboardInterrupt):
        _run(tmp_path, f'''
            import os, sys, time
            from pathlib import Path
            Path({str(pid)!r}).write_text(str(os.getpid()))
            print('ready', file=sys.stderr, flush=True)
            time.sleep(10)
        ''', on_line=interrupt)
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid.read_text()), 0)
    assert seen == [b"ready"]


def test_older_runtime_human_stderr_is_ignored_without_new_cli_flag(tmp_path):
    events = []
    with update_operation("github", callback=events.append):
        result = _run(tmp_path, f'''
            import sys
            print({SECRET!r}, file=sys.stderr, flush=True)
            print('{{"ok":true}}')
        ''', on_line=child_progress_receiver())
    assert result.returncode == 0
    assert json.loads(result.stdout)["ok"] is True
    assert SECRET not in repr(events)


@pytest.mark.parametrize("fail", [False, True])
def test_dev_cli_jsonl_capability_keeps_one_stdout_document(monkeypatch, capsys, fail):
    monkeypatch.setenv(update_child.PROGRESS_ENV, "1")
    def install(*_args, **_kwargs):
        for i in range(1, 4):
            with update_phase("plugin_install", plugin_index=i, plugin_total=3):
                if fail and i == 2:
                    raise dev_updater.DevUpdateError("fixture_failure")
        return {"ok": True}
    monkeypatch.setattr(dev_updater, "apply_snapshot", install)
    status = dev_updater.main(["synthetic.zip", "--json"])
    output = capsys.readouterr()
    assert json.loads(output.out)["ok"] is (not fail)
    assert status == int(fail)
    phases = [json.loads(line) for line in output.err.splitlines()]
    ordinal = [(e["plugin_index"], e["status"]) for e in phases if e["stage"] == "plugin_install"]
    assert ordinal == ([(1,"RUNNING"),(1,"OK"),(2,"RUNNING"),(2,"ERROR")] if fail else
                       [(1,"RUNNING"),(1,"OK"),(2,"RUNNING"),(2,"OK"),(3,"RUNNING"),(3,"OK")])
    assert phases[-1]["outcome"] == ("failed" if fail else "success")


@pytest.mark.parametrize("failure", [OSError(SECRET), UnicodeError(SECRET),
                                    update_child.ChildOutputError(SECRET), TimeoutError(SECRET)])
def test_apply_child_errors_are_fixed_and_unchained(tmp_path, monkeypatch, failure):
    executable = tmp_path / "command"
    executable.touch()
    monkeypatch.setattr(updater, "_command_path", lambda *_args: executable)
    def failed(*_args, **_kwargs):
        raise failure
    monkeypatch.setattr(updater, "run_plugin_child", failed)
    with pytest.raises((updater.UpdateError, TimeoutError)) as captured:
        updater._apply_release_plugin_snapshot(tmp_path, tmp_path / "snapshot")
    assert SECRET not in str(captured.value)
    assert captured.value.__suppress_context__


@pytest.mark.parametrize("boundary", ["failed_exit", "timeout", "interrupted"])
def test_real_child_is_reaped_before_transaction_restores_packages(tmp_path, monkeypatch, boundary):
    from tests.test_updater import _transactional_update_fixture, _release_with_plugin_manifest
    real_apply = updater._apply_release_plugin_snapshot
    root, _old, packages, _baseline = _transactional_update_fixture(tmp_path, monkeypatch)
    before_core = updater._symlink_target(root / "current").resolve()
    before_packages = {key: updater._package_identity(key)
                       for key in ("org.test", "org.only-b", "org.third-party")}
    source = _release_with_plugin_manifest(tmp_path / "next", "3.0.0")
    source_command = tmp_path / "existing-child-command"
    source_command.touch()
    original_command_path = updater._command_path
    monkeypatch.setattr(updater, "_command_path", lambda release, name:
                        source_command if name == updater.DEV_COMMAND else original_command_path(release, name))
    children = []
    real_popen = subprocess.Popen
    def track(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(update_child.subprocess, "Popen", track)
    payload = packages / "org.test" / "1.0.0" / "payload.bin"
    terminal = "sys.exit(1)" if boundary == "failed_exit" else "time.sleep(10)"
    def run(_command, *, timeout, on_line):
        def receive(line):
            on_line(line)
            if boundary == "interrupted":
                raise KeyboardInterrupt()
        return _run(tmp_path, f'''
            import sys, time, json
            from pathlib import Path
            Path({str(payload)!r}).write_bytes(b"partly changed by child")
            print(json.dumps({{"schema":"cifereca.update.progress/1", "stage":"plugin_install",
                "status":"RUNNING", "plugin_index":2, "plugin_total":3}}), file=sys.stderr, flush=True)
            {terminal}
        ''', on_line=receive, timeout=.4)
    monkeypatch.setattr(updater, "run_plugin_child", run)
    monkeypatch.setattr(updater, "_apply_release_plugin_snapshot", real_apply)
    restore = updater._restore_transaction_packages
    restored = []
    def verified_restore(*args, **kwargs):
        assert len(children) == 1 and children[0].poll() is not None
        restored.append(True)
        return restore(*args, **kwargs)
    monkeypatch.setattr(updater, "_restore_transaction_packages", verified_restore)
    events = []
    expected = {"failed_exit": updater.UpdateError, "timeout": TimeoutError,
                "interrupted": KeyboardInterrupt}[boundary]
    with pytest.raises(expected):
        with update_operation("source", callback=events.append):
            updater.install_source(source)
    assert restored == [True]
    assert updater._symlink_target(root / "current").resolve() == before_core
    assert {key: updater._package_identity(key) for key in before_packages} == before_packages
    assert [e["status"] for e in events if e["stage"] == "rollback"] == ["ROLLBACK", "OK"]
    assert [(e["plugin_index"], e["plugin_total"]) for e in events if e["stage"] == "plugin_install"] == [(2, 3)]
    assert events[-1]["outcome"] == "failed"
