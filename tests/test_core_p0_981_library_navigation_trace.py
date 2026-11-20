"""#981: distinguish passive observation from real refresh and invalidation."""
import logging

from cifereca.gui.library_refresh import passive_library_refresh, invalidate_library
from tests.test_core_ui_identity_async_recent import _harness


def _events(caplog):
    return [record.getMessage() for record in caplog.records
            if record.getMessage().startswith("TRACE library refresh ")]


def test_navigation_and_publication_have_distinct_causes_and_counts(caplog):
    host = _harness()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        passive_library_refresh(host)
        first = host.runner.requests[-1]
        for _ in range(5):
            passive_library_refresh(host)
        assert len(host.runner.requests) == 1
        invalidate_library(host)
        first.ok(first.work())
        assert len(host.runner.requests) == 2
        second = host.runner.requests[-1]
        second.ok(second.work())
    events = _events(caplog)
    assert sum("cause=navigation decision=delegate" in e for e in events) == 1
    assert sum("cause=navigation decision=join request_generation=1 " in e for e in events) == 5
    assert sum("cause=publication decision=follow_up request_generation=1 " in e for e in events) == 1
    assert sum("cause=automatic decision=admitted" in e for e in events) == 2
    assert sum("cause=completion decision=rendered request_generation=2 " in e for e in events) == 1
    assert all("identity_generation=" in e and "scope_generation=" in e
               and "source_epoch=" in e and "mode=profile" in e for e in events)
    assert "alice" not in "\n".join(events)
    assert len(host.library_shown) == 1


def test_explicit_refresh_remains_independent_of_passive_join(caplog):
    host = _harness()
    with caplog.at_level(logging.INFO, logger="cifereca"):
        passive_library_refresh(host)
        passive_library_refresh(host)
        host.refresh_library(False, control=object())
        assert len(host.runner.requests) == 2
        newest = host.runner.requests[-1]
        newest.ok(newest.work())
    events = _events(caplog)
    assert any("cause=user decision=admitted request_generation=2 " in e for e in events)
    assert any("cause=navigation decision=join request_generation=1 " in e for e in events)
    assert any("cause=completion decision=rendered request_generation=2 " in e for e in events)
