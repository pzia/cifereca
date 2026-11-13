"""P0 #5851: installed ADEPT inventory capability independent of acquisition."""
from types import SimpleNamespace as NS

import pytest
from cifereca.plugin_api import AttentionRequired
from cifereca.services import LibraryService


def _setup():
    descriptor = NS(id="runtime.synthetic", type="runtime.adapter",
                    capabilities=("loan.inventory.readonly",))
    status = NS(descriptor=descriptor, enabled=True, state="ready")
    reads = []
    runtime = NS(loan_inventories=lambda names: reads.append(tuple(names)) or {
        "complete": True, "profiles": [{"profile": names[0], "loans": []}]})
    live = {"status": status, "runtime": runtime, "candidates": [status]}
    manager = NS(
        candidates=lambda category, **kw: live["candidates"],
        status=lambda plugin_id: live["status"],
        service=lambda plugin_id: live["runtime"])
    core = NS(_plugin_manager=manager,
              current_identity=NS(identity_id="bob"),
              identity_by_id=lambda identity: NS(identity_id=identity)
              if identity == "alice" else None)
    row = {"identity_id": "alice", "provider_id": "fixture-lender",
           "credential_id": "reader", "account_id": "linked"}
    return core, row, live, reads


def reader(core, row):
    return LibraryService.adept_inventory_reader_for_acquisition(core, row)


def denied(fn):
    with pytest.raises(AttentionRequired) as error:
        fn()
    assert error.value.code == "adept_inventory_unavailable"


def test_explicit_identity_ignores_gtk_and_provider_brand():
    core, row, _, reads = _setup()
    assert reader(core, row)(["profile-a"]) == {
        "complete": True, "profiles": [{"profile": "profile-a", "loans": []}]}
    assert reads == [("profile-a",)]


@pytest.mark.parametrize("case", ["missing", "disabled", "ambiguous", "none"])
def test_missing_ambiguous_or_disabled_read_capability_closes(case):
    core, row, live, reads = _setup()
    if case == "missing":
        live["status"].descriptor.capabilities = ()
    elif case == "disabled":
        live["status"].state = "blocked"
    elif case == "ambiguous":
        live["candidates"].append(NS(
            descriptor=NS(id="runtime.other", type="runtime.adapter",
                          capabilities=("loan.inventory.readonly",)),
            enabled=True, state="ready"))
    else:
        core._plugin_manager = None
    denied(lambda: reader(core, row))
    assert reads == []


@pytest.mark.parametrize("case", ["replace", "disable", "compete"])
def test_lease_rechecks_runtime_before_call(case):
    core, row, live, reads = _setup()
    read = reader(core, row)
    if case == "replace":
        live["runtime"] = NS(loan_inventories=lambda names: pytest.fail("replaced"))
    elif case == "disable":
        live["status"].state = "blocked"
    else:
        live["candidates"].append(NS(
            descriptor=NS(id="runtime.other", type="runtime.adapter",
                          capabilities=("loan.inventory.readonly",)),
            enabled=True, state="ready"))
    denied(lambda: read(["profile-a"]))
    assert reads == []


def test_lease_rechecks_runtime_after_call():
    core, row, live, reads = _setup()
    read = reader(core, row)
    def changed(names):
        reads.append(tuple(names))
        live["status"].state = "blocked"
        return {"complete": True, "profiles": []}
    live["runtime"].loan_inventories = changed
    denied(lambda: read(["profile-a"]))
    assert reads == [("profile-a",)]


@pytest.mark.parametrize("names", [{}, [], ["profile-a"] * 2,
                                     ["profile-a"] * 9, [object()], [""]])
def test_lease_does_not_pass_malformed_candidates_to_runtime(names):
    core, row, _, reads = _setup()
    read = reader(core, row)
    denied(lambda: read(names))
    assert reads == []


def test_nonexistent_identity_or_non_native_provenance_is_not_a_runtime_scope():
    core, row, _, reads = _setup()
    denied(lambda: reader(core, {**row, "identity_id": "bob"}))
    denied(lambda: reader(core, {**row, "credential_id": object()}))
    assert reads == []


def test_untrusted_runtime_payload_cannot_be_a_complete_inventory():
    core, row, live, _ = _setup()
    live["runtime"].loan_inventories = lambda names: {
        "complete": "yes", "profiles": []}
    denied(lambda: reader(core, row)(["profile-a"]))
