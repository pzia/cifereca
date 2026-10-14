"""#1498: destructive duplicate cleanup uses real pair counts only."""
import pytest
from cifereca.plugin_api import LibraryMutationResult, MaintenancePlan
from cifereca_library_audits import DuplicateCleanupTool


class Broker:
    def __init__(self):
        self.requests = []
        self.fail_on = None
        self.cancel_on = None

    def plan(self, request):
        self.requests.append(request)
        return request

    def run(self, request, control):
        if request.placement_id == self.fail_on:
            raise OSError("/private/secret.pdf")
        if request.placement_id == self.cancel_on:
            control.stopped = True
        return LibraryMutationResult(True, changed=1, summary="ok")


class Context:
    def __init__(self, broker):
        self.mutation = broker

    def broker(self, name):
        assert name == "library.maintenance"
        return self.mutation


class Cancelled(Exception):
    pass


class Control:
    def __init__(self):
        self.events = []
        self.stopped = False

    def check_cancelled(self):
        if self.stopped:
            raise Cancelled()

    def report(self, **kwargs):
        self.events.append(kwargs)


def _case(pairs, broker=None):
    broker = broker or Broker()
    tool = DuplicateCleanupTool().activate(Context(broker))
    plan = MaintenancePlan(tool_id=tool.info.id, summary="Plan", facts={"pairs": pairs})
    return tool, plan, Control(), broker


def test_zero_duplicates_reports_activity_without_1_of_1():
    tool, plan, control, broker = _case(())
    result = tool.run(plan, control)
    assert result.ok and result.changed == 0 and result.facts["planned"] == 0
    assert broker.requests == []
    assert len(control.events) == 2
    assert all("current" not in e and "total" not in e for e in control.events)


def test_duplicate_cleanup_counts_attempted_pairs_not_files():
    tool, plan, control, broker = _case((("p2", "p1"), ("p3", "p1")))
    result = tool.run(plan, control)
    assert result.ok and result.changed == 2
    assert [(e["current"], e["total"]) for e in control.events] == [(0, 2), (1, 2), (2, 2)]
    assert [(r.operation, r.placement_id, r.keep_placement_id) for r in broker.requests] == [
        ("delete_duplicate", "p2", "p1"),
        ("delete_duplicate", "p3", "p1"),
    ]


def test_failed_duplicate_cleanup_shows_partial_failure_without_secrets():
    broker = Broker()
    broker.fail_on = "p3"
    tool, plan, control, _ = _case((("p2", "p1"), ("p3", "p1")), broker)
    result = tool.run(plan, control)
    assert not result.ok and result.changed == 1
    assert result.facts["failures"] == 1
    assert "échec" in control.events[-1]["message"]
    assert all("/private/" not in text and "p3" not in text for text in result.warnings)
    assert all("/private/" not in e["message"] for e in control.events)


def test_cancellation_after_last_delete_never_reports_complete():
    broker = Broker()
    broker.cancel_on = "p2"
    tool, plan, control, _ = _case((("p2", "p1"),), broker)
    with pytest.raises(Cancelled):
        tool.run(plan, control)
    assert [(e["current"], e["total"]) for e in control.events] == [(0, 1)]


def test_cancellation_before_any_delete_has_no_mutation():
    tool, plan, control, broker = _case((("p2", "p1"),))
    control.stopped = True
    with pytest.raises(Cancelled):
        tool.run(plan, control)
    assert broker.requests == []
    assert control.events == []


def test_core_warning_text_is_never_exposed_through_destructive_result():
    class WarningBroker(Broker):
        def run(self, request, control):
            return LibraryMutationResult(
                False, changed=1, summary="PRIVATE_RESULT_TITLE",
                warnings=(
                    "Bearer PRIVATE_ACCESS_TOKEN",
                    "/home/PRIVATE_USER/books/secret.epub",
                ),
            )

    broker = WarningBroker()
    tool, plan, control, _ = _case((("PRIVATE_DUPLICATE", "PRIVATE_KEEP"),), broker)
    result = tool.run(plan, control)
    assert not result.ok and result.changed == 1
    assert result.facts == {"planned": 1, "failures": 1}
    assert result.warnings == (
        "Doublon 1/1 : 2 avertissement(s) du service",
    )
    assert [(event.get("current"), event.get("total"))
            for event in control.events] == [(0, 1), (1, 1)]
    assert "échec" in control.events[-1]["message"]
    for secret in ("PRIVATE", "Bearer", "secret.epub", "/home/"):
        assert secret not in str(result.warnings)
        assert secret not in result.summary
        assert all(secret not in event["message"] for event in control.events)


def test_nonsequence_mutation_warning_is_summarized_not_iterated():
    class WarningBroker(Broker):
        def run(self, request, control):
            return type("Result", (), {
                "ok": True, "changed": 0,
                "warnings": "Bearer PRIVATE_TOKEN",
            })()

    broker = WarningBroker()
    tool, plan, control, _ = _case((("duplicate", "keeper"),), broker)
    result = tool.run(plan, control)
    assert result.ok and result.changed == 0
    assert result.warnings == (
        "Doublon 1/1 : 1 avertissement(s) du service",
    )


@pytest.mark.parametrize("step", ("plan", "run"))
def test_broker_exception_class_and_message_never_leak_from_duplicate_cleanup(step):
    """An untrusted broker may generate an exception class containing a secret."""
    SecretFailure = type("Bearer_PRIVATE_TOKEN_home_PRIVATE_PATH", (OSError,), {})

    class PoisonedBroker(Broker):
        def plan(self, request):
            self.requests.append(request)
            if request.placement_id == "PRIVATE_DUPLICATE" and step == "plan":
                raise SecretFailure("PRIVATE_PASSWORD /home/PRIVATE_USER/secret.epub")
            return request

        def run(self, request, control):
            if request.placement_id == "PRIVATE_DUPLICATE" and step == "run":
                raise SecretFailure("PRIVATE_PASSWORD /home/PRIVATE_USER/secret.epub")
            return LibraryMutationResult(True, changed=1, summary="PRIVATE_CORE_TITLE")

    broker = PoisonedBroker()
    tool, plan, control, _ = _case((
        ("PRIVATE_DUPLICATE", "PRIVATE_KEEP"),
        ("other-duplicate", "other-keep"),
    ), broker)
    result = tool.run(plan, control)
    assert result.ok is False
    assert result.changed == 1
    assert result.facts == {"planned": 2, "failures": 1}
    assert result.warnings == ("Doublon 1/2 : échec du service",)
    assert [
        (event.get("current"), event.get("total")) for event in control.events
    ] == [(0, 2), (1, 2), (2, 2)]
    assert "échec" in control.events[-1]["message"]
    assert [request.placement_id for request in broker.requests] == [
        "PRIVATE_DUPLICATE", "other-duplicate",
    ]
    for secret in ("PRIVATE", "Bearer", "TOKEN", "PASSWORD", "/home/", "OSError"):
        assert secret not in str(result.warnings)
        assert secret not in result.summary
        assert all(secret not in event["message"] for event in control.events)
