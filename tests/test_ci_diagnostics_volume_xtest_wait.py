"""GTK CI: Ctrl+End evidence waits for the real XTEST-driven scroll."""
from pathlib import Path


PROBE = Path(__file__).resolve().parents[1] / "e2e/diagnostics_volume_probe.py"


def test_ctrl_end_xtest_has_bounded_async_scroll_proof_not_fake_position():
    code = PROBE.read_text(encoding="utf-8")
    section = code.split("    def kobo_detail_visible():", 1)[1].split(
        "    def verify_kobo_detail_keyboard_end(", 1
    )[0]
    assert 'device[0].key("Control_L", True)' in section
    assert 'device[0].key("End", True)' in section
    assert "until(" in section
    assert "adjustment.get_value()" in section
    assert "seconds=3" in section
    assert "later(lambda: verify_kobo_detail_keyboard_end" not in section
    assert "adjustment.set_value(bottom)" not in section
    # A second independent check must still assert keyboard reachability.
    after = code.split("    def verify_kobo_detail_keyboard_end(", 1)[1]
    assert "assert adjustment.get_value() >= bottom - 5" in after
    assert "len(broker.requests) == query_count" in after
