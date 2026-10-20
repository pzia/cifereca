"""P0 #1120: real installed Core callback from a synthetic, read-only ADEPT CLI.

The subprocess is always a stub: no ADEPT binary, profile, Adobe entitlement,
loan return, ACSM replay, provider authentication or network operation exists.
"""
from __future__ import annotations

import importlib
import json
import logging
from pathlib import Path
import subprocess

import pytest

from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from tests.test_adept_shared_diagnostics import install_runtime

PRIVATE_PROFILE = "PRIVATE_BORROWER_PROFILE"
PRIVATE_TOKEN = "https://private.invalid/loan?token=PRIVATE_ACCESS"
PRIVATE_TITLE = "PRIVATE_USER_BOOK"


@pytest.mark.parametrize("reply,reason", [
    ({"loans": [{"title": PRIVATE_TITLE}], "url": PRIVATE_TOKEN}, "ok_missing"),
    ({"ok": "true", "profile": PRIVATE_PROFILE}, "ok_not_boolean"),
    ({"ok": True, "error": {"url": PRIVATE_TOKEN}}, "success_with_error"),
])
def test_installed_runtime_readonly_loans_list_has_identical_closed_causes_on_two_channels(
    tmp_path, monkeypatch, reply, reason,
):
    log = tmp_path / "cifereca.log"
    logger = logging.getLogger("cifereca")
    old_level = logger.level
    sink = logging.FileHandler(log, encoding="utf-8")
    sink.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(sink)
    logger.setLevel(logging.INFO)

    manager = None
    try:
        manager, context, diagnostics = install_runtime(tmp_path)
        root = Path(__file__).resolve().parents[1] / "src/plugins/runtime-kobo-adept"
        monkeypatch.syspath_prepend(str(root))
        runtime_module = importlib.import_module("cifereca_kobo_adept_runtime")
        cli = runtime_module.KoboAdeptRuntime(context)

        invocations = []
        raw = json.dumps(reply).encode("utf-8")
        def fake_cli(argv, **kwargs):
            invocations.append((list(argv), kwargs))
            return subprocess.CompletedProcess(argv, 0, raw, b"")

        monkeypatch.setattr(runtime_module.subprocess, "run", fake_cli)
        with pytest.raises(runtime_module.KoboAdeptError) as caught:
            cli.run_json([
                "loans", "list", "--profile", PRIVATE_PROFILE, "--json",
            ], timeout=30, max_output_bytes=4 * 1024 * 1024)
        assert caught.value.code == "kobo_adept_invalid_response"
        assert caught.value.retryable is False
        assert cli.last_run_diagnostic["validation_reason"] == reason
        assert len(invocations) == 1
        argv, kwargs = invocations[0]
        assert argv == ["kobo-adept", "loans", "list", "--profile",
                        PRIVATE_PROFILE, "--json"]
        assert kwargs["input"] is None
        assert kwargs["capture_output"] is True

        feed = query_diagnostic_events(diagnostics, log_path=log, limit=100)
        facts = [
            event for event in feed["events"]
            if event.get("detail", {}).get("operation_kind") == "kobo_adept"
        ]
        assert len(facts) == 2
        by_source = {row["source"]: row for row in facts}
        assert set(by_source) == {"core_events", "app_log"}
        assert by_source["core_events"]["detail"] == by_source["app_log"]["detail"]
        detail = by_source["core_events"]["detail"]
        assert detail["status"] == "failed"
        assert detail["operation"] == "loans list"
        assert detail["code"] == "kobo_adept_invalid_response"
        assert detail["validation_reason"] == reason
        assert detail["retryable"] is False
        assert detail["returncode"] == 0
        assert detail["stdout_bytes"] == len(raw)
        assert detail["stderr_bytes"] == 0
        assert by_source["core_events"]["event_id"] != by_source["app_log"]["event_id"]
        assert all("task_ref" not in row for row in facts)
        public = json.dumps(feed) + log.read_text(encoding="utf-8")
        for forbidden in (PRIVATE_PROFILE, PRIVATE_TOKEN, PRIVATE_TITLE,
                          "PRIVATE_BORROWER", "PRIVATE_ACCESS"):
            assert forbidden not in public
    finally:
        if manager is not None:
            manager.close()
        logger.removeHandler(sink)
        logger.setLevel(old_level)
        sink.close()
