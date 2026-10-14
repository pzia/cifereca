"""Bounded GUI feedback timeouts (#1132)."""

from cifereca.gui.timeout_policy import bounded_seconds


def test_normal_duration_is_preserved():
    assert bounded_seconds(5) == 5


def test_zero_negative_and_huge_values_are_bounded():
    assert bounded_seconds(0) == 1
    assert bounded_seconds(-7) == 1
    assert bounded_seconds(9999999) == 10


def test_untrusted_types_revert_to_default():
    for value in (None, True, "999999", 3.4, object()):
        assert bounded_seconds(value) == 3


def test_activity_delay_has_independent_default_and_limit():
    assert bounded_seconds(None, maximum=3600, default=4) == 4
    assert bounded_seconds(99999, maximum=3600, default=4) == 3600
