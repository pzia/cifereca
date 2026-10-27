"""#3122: real BnF adapter must fail closed when loan HTML crosses a scope race.

The client and active account are synthetic. The test invokes neither
network nor ACSM, Adobe, borrow, return, deletion or credential exchange.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import time

from cifereca.plugin_api import read_loan_inventory
from tests.test_core_p1_3122_paris_bnf_snapshots import bnf, BNF_HTML


def test_bnf_identity_changed_during_html_read_publishes_no_old_loans():
    session, account, client = bnf()
    entered, release = Event(), Event()
    original_activity = client.activity

    def delayed_activity():
        entered.set()
        assert release.wait(5), "synthetic BnF activity release timed out"
        return original_activity()

    client.activity = delayed_activity
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(read_loan_inventory, session, account)
            try:
                assert entered.wait(5), "synthetic BnF activity never started"
                # Account instances are mutable across the Core, even while
                # a provider holds its own lock.
                account.identity_id = "other-identity"
            finally:
                release.set()
            result = future.result(timeout=10)
        assert result.status == "failed"
        assert result.loans == ()
        assert client.calls.count("activity") == 1
        assert "loan_token" not in client.calls
    finally:
        release.set()
        session.close()


def test_bnf_close_during_html_read_revokes_snapshot_before_publish():
    session, account, client = bnf()
    entered, release = Event(), Event()
    original_activity = client.activity

    def delayed_activity():
        entered.set()
        assert release.wait(5), "synthetic BnF activity release timed out"
        return original_activity()

    client.activity = delayed_activity
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            reader = pool.submit(read_loan_inventory, session, account)
            try:
                assert entered.wait(5), "synthetic BnF activity never started"
                closer = pool.submit(session.close)
                # close() revokes the epoch outside the HTTP lock.
                deadline = time.monotonic() + 5
                while not session._closed and time.monotonic() < deadline:
                    time.sleep(0.005)
                assert session._closed
            finally:
                release.set()
            result = reader.result(timeout=10)
            closer.result(timeout=10)
        assert result.status == "failed"
        assert result.loans == ()
        assert not session.connected and session._accounts == []
        assert client.calls.count("activity") == 1
        assert "loan_token" not in client.calls
    finally:
        release.set()
        session.close()
