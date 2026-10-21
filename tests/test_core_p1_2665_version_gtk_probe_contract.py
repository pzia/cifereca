"""#2665: version clipboard acceptance runs inside real GTK/X11 diagnostics E2E.

Static guard: the E2E probe must use clipboard readback, not merely look at
labels or an implementation-only fake clipboard. Private data stays local.
"""
from pathlib import Path


def test_diagnostics_probe_copies_actual_gtk_clipboard_and_refreshes_versions():
    source = (Path(__file__).resolve().parents[1] /
              "e2e/diagnostics_page_probe.py").read_text(encoding="utf-8")
    for required in (
        'tab_key("État système", "system", versions_initial)',
        'keyboard_action("Copier les versions", versions_clipboard_first)',
        'keyboard_action("Copier les versions", versions_clipboard_updated)',
        "clipboard.read_text_finish(task)",
        "get_clipboard().read_text_async(None, copied)",
        'broker.recorder_version = "0.1.5"',
        'broker.core_version = "0.9.3"',
        '"P1_2665_exact_versions_clipboard_no_support_secrets"',
        '"P1_2665_refresh_replaces_clipboard_snapshot"',
        '"PRIVATE_ACCOUNT_2665"',
        '"PRIVATE_VERSION_TOKEN_2665"',
    ):
        assert required in source
