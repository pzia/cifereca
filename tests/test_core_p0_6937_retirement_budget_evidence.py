"""#6937: arithmetic bound for the separate terminal retirement trace budget.

Measure the canonical maximal schema rather than extrapolating from one
fixture's short inode/mtime numbers. No write or private production fence.
"""
from cifereca.core import derived_variant_journal as journal
from cifereca.core import derived_variant_private_retirement as retirement


def test_terminal_trace_upper_bound_uses_full_int64_fields():
    maximum = 2 ** 63 - 1
    before = [maximum] * 5
    base = {
        "schema": retirement._SCHEMA,
        "receipt": "f" * 64, "binding": "e" * 64,
        "nonce": "a" * 32,
        "state": "prepared", "before": before,
        "after": None, "region": [maximum, maximum],
        "destination": [maximum, maximum],
        "mount": maximum, "outcome": None,
    }
    key = retirement._PREFIX + base["receipt"]
    prepared = journal._encode(base)
    assert retirement._decode_trace(key, prepared) == base

    for outcome in ("unlink_confirmed", "absence_reconciled"):
        terminal = dict(base, state="retired", after=before,
                        outcome=outcome)
        raw = journal._encode(terminal)
        assert retirement._decode_trace(key, raw) == terminal
        assert len(raw.encode("ascii")) <= retirement._MAX_RECORD_BYTES
        assert len(raw.encode("ascii")) > len(prepared.encode("ascii"))

    # One fully reserved trace per maximum journal record, in a distinct
    # namespace. The original 8 MiB/16 GiB admission budgets are unchanged.
    assert retirement._MAX_RECORDS == journal._MAX_RECORDS == 1024
    assert retirement._MAX_RECORD_BYTES == 2048
    assert retirement._MAX_TOTAL_BYTES == 1024 * 2048
    assert journal._MAX_TOTAL_BYTES == 8 * 1024 * 1024
    assert journal._MAX_RESERVED_OUTPUT_BYTES == 16 * 1024 ** 3
