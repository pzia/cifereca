"""#4882: explicit scoped Kobo CLI trace controls private URL visibility.

All providers are synthetic; no live network and no real credentials/URLs.
"""
import json
import logging
import stat
from types import SimpleNamespace

import pytest

from cifereca import cli, logging_utils
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level
from cifereca.plugin_api import HTTPX_TRANSPORT


@pytest.fixture
def reset_level(monkeypatch, tmp_path):
    previous = diagnostics_level()
    logger = logging.getLogger("cifereca")
    old_handlers = tuple(logger.handlers)
    for handler in old_handlers:
        logger.removeHandler(handler)
    destination = tmp_path / "cifereca.log"
    monkeypatch.setattr(logging_utils, "log_file", lambda: destination)
    set_diagnostics_level("normal")
    try:
        yield destination
    finally:
        for handler in tuple(logger.handlers):
            logger.removeHandler(handler)
            if handler not in old_handlers:
                handler.close()
        for handler in old_handlers:
            logger.addHandler(handler)
        set_diagnostics_level(previous)


def _core(monkeypatch, calls):
    service = SimpleNamespace(
        connect_kobo_selected_edition=lambda credential: calls.append(
            ("connect", credential, diagnostics_level(),
             HTTPX_TRANSPORT.trace_enabled())
        ),
        synchronize_kobo_selected_edition=lambda credential, item, *,
        explicit_user_action: (
            calls.append(("sync", credential, item, explicit_user_action))
            or {"ok": True, "status": "present_attested", "changed": 0}
        ),
    )
    def opened(**kwargs):
        calls.append(("open", kwargs))
        return SimpleNamespace(
            library=service, close=lambda: calls.append(("close",)),
        )
    monkeypatch.setattr(cli.CiferecaCore, "open", opened)
    return service


@pytest.mark.parametrize(("flag", "expected_level", "active"), [
    ([], "detailed", False),
    (["--trace"], "trace", True),
])
def test_exact_kobo_cli_trace_only_when_explicitly_selected(
    monkeypatch, capsys, reset_level, flag, expected_level, active,
):
    calls = []
    _core(monkeypatch, calls)
    assert cli.main([
        "kobo-sync-edition", "--credential", "synthetic",
        "--item", "synthetic-book", "--confirm", *flag,
    ]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "present_attested"
    assert calls[0] == ("open", {
        "diagnostics_enabled": True, "diagnostics_level": expected_level,
    })
    assert calls[1] == (
        "connect", "synthetic", "trace" if active else "normal", active,
    )
    assert calls[2] == ("sync", "synthetic", "synthetic-book", True)
    assert calls[3] == ("close",)
    assert diagnostics_level() == "normal"
    assert HTTPX_TRANSPORT.trace_enabled() is False


def test_private_trace_flag_never_bypasses_confirmation_or_opens_core(
    monkeypatch, capsys, reset_level,
):
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **kw: pytest.fail("No Core startup without --confirm"),
    )
    assert cli.main([
        "kobo-sync-edition", "--credential", "synthetic",
        "--item", "synthetic-book", "--trace",
    ]) == 2
    assert json.loads(capsys.readouterr().out)["code"] == (
        "explicit_edition_confirmation_required"
    )
    assert HTTPX_TRANSPORT.trace_enabled() is False


def test_exact_cli_trace_writes_0600_private_journal_without_stdout_leak(
    monkeypatch, capsys, reset_level,
):
    calls = []
    service = _core(monkeypatch, calls)
    signed = "https://cdn.example/file.epub?signature=PRIVATE_SYNTHETIC"
    def connect(credential):
        logging.getLogger("cifereca").debug(
            "TRACE kobo private_candidate_url phase=offer url=%r", signed,
        )
    service.connect_kobo_selected_edition = connect
    before = tuple(logging.getLogger("cifereca").handlers)
    assert cli.main([
        "kobo-sync-edition", "--credential", "synthetic",
        "--item", "book-1", "--confirm", "--trace",
    ]) == 0
    text = reset_level.read_text(encoding="utf-8")
    assert "TRACE kobo private_candidate_url" in text
    assert repr(signed) in text
    assert stat.S_IMODE(reset_level.stat().st_mode) == 0o600
    assert tuple(logging.getLogger("cifereca").handlers) == before
    assert signed not in capsys.readouterr().out
    assert diagnostics_level() == "normal"
