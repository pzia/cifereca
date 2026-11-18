"""#5833: inventory candidates, never infer safety from an unresolved worker."""
from scripts.ci_task_admission_inventory import inventory_source


def test_lexical_worker_and_callback_are_distinguished():
    rows = inventory_source("""
def controller(window):
    def worker():
        return window.service.current_account.history()
    def done(result):
        if window.service.current_identity:
            repaint(result)
    window.runner.submit("read", worker, done)
    window.runner.submit("other", lambda: fixed_call(), done)
""", "synthetic.py")
    first, second = rows
    assert first["worker_resolution"] == "lexical"
    assert first["navigation_signals"] == ["current_account"]
    assert second["worker_resolution"] == "lexical"
    assert second["navigation_signals"] == []


def test_dynamic_alias_and_ambiguous_worker_are_visible():
    rows = inventory_source("""
def controller(runner, condition):
    callback = getattr(runner, "submit")
    alias = runner.submit
    if condition:
        work = lambda: source_a()
    else:
        work = lambda: source_b()
    runner.submit("read", work)
    runner.submit_batch(requests)
    tasks.submit(lambda ctl: ctl.check_cancelled())
""", "synthetic.py")
    assert [r["kind"] for r in rows] == [
        "dynamic_reference", "reference", "call", "call", "call",
    ]
    assert rows[2]["worker_resolution"] == rows[3]["worker_resolution"] == "unresolved"
    assert rows[4]["guard_calls_to_review"] == ["check_cancelled"]
