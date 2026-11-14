"""P1 #6002 — Home's public broker-only, current-profile loan panel."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import DigitalLoanAvailability, digital_loan_availability_lines

import cifereca_home as home


def capacity(identity, *, borrower="primary", ceiling=4, observed=2):
    return DigitalLoanAvailability(
        identity_id=identity, provider_id="synthetic", credential_id="CREDENTIAL_PRIVATE",
        borrower_id=borrower, provider_label="Bibliothèque témoin",
        connection_label="Connexion 1", borrower_label="Compte principal",
        personal_ceiling=ceiling, observed_loans=observed,
    )


def test_formatting_is_unknown_despite_manual_ceiling_and_positive_cache():
    rows = (capacity("alice"), capacity("alice", borrower="linked", ceiling=0, observed=1))
    captions = digital_loan_availability_lines(rows, identity_id="alice")
    assert len(captions) == 3
    assert all("Reste inconnu" in x for x in captions[:2])
    assert any("plafond personnel 4 (indicatif)" in x for x in captions)
    assert any("plafond personnel 0 (indicatif)" in x for x in captions)
    assert captions[-1].startswith("Total non calculable")
    assert "CREDENTIAL_PRIVATE" not in str(captions)
    assert not any("reste 2" in x.lower() for x in captions)


def test_mixed_or_malformed_broker_rows_fail_closed_without_raw_echo():
    valid = capacity("alice")
    for incoming in (
        [valid, capacity("bob")],
        [valid, {"provider_label": "https://private/?token=SECRET"}],
        "private string", {0: valid},
        [valid] * 129,
    ):
        lines = digital_loan_availability_lines(incoming, identity_id="alice")
        assert lines == ("Reste inconnu — projection des comptes invalide.",) if (
            type(incoming) is list and len(incoming) < 129
        ) else lines == ("Reste inconnu — projection des comptes indisponible.",)
        assert "PRIVATE" not in str(lines) and "SECRET" not in str(lines)
    assert digital_loan_availability_lines((), identity_id="alice") == (
        "Reste inconnu — aucun compte emprunteur vérifié.",
    )


def test_home_controller_queries_only_selected_identity_via_public_broker():
    data = [
        {"identity_id": "alice", "label": "Alice", "selected": True},
        {"identity_id": "bob", "label": "Bob", "selected": False},
    ]
    callbacks, queries, rendered = [], [], []
    host = NS(
        identity_overview=lambda: list(data),
        subscribe_identity_overview=lambda cb: (
            callbacks.append(cb) or (lambda: callbacks.remove(cb))
        ),
    )
    broker = NS(
        identities=lambda: [
            {"identity_id": "alice", "label": "Alice"},
            {"identity_id": "bob", "label": "Bob"},
        ],
        digital_loan_availability=lambda iid: (
            queries.append(iid) or (capacity(iid),)
        ),
    )
    controller = home.OverviewController(host, rendered.append, broker)
    try:
        assert queries == ["alice"]
        assert "digital_loan_availability" in rendered[-1][0]
        assert "digital_loan_availability" not in rendered[-1][1]
        data[0]["selected"], data[1]["selected"] = False, True
        callbacks[0]()
        assert queries == ["alice", "bob"]
        assert "digital_loan_availability" not in rendered[-1][0]
        assert rendered[-1][1]["digital_loan_availability"][0].identity_id == "bob"
        assert "digital_loan_availability" not in data[0]
    finally:
        controller.close()
    assert not callbacks


def test_home_broker_unavailable_does_not_call_provider_or_claim_remaining():
    data = [{"identity_id": "alice", "label": "Alice", "selected": True}]
    rendered, callbacks = [], []
    host = NS(
        identity_overview=lambda: data,
        subscribe_identity_overview=lambda cb: (
            callbacks.append(cb) or (lambda: callbacks.remove(cb))
        ),
    )
    controller = home.OverviewController(
        host, rendered.append, identity_broker=NS(
            identities=lambda: [],
            digital_loan_availability=lambda _identity: (),
        ),
    )
    try:
        assert rendered[-1][0].get("digital_loan_availability") == ()
        assert digital_loan_availability_lines(
            rendered[-1][0].get("digital_loan_availability", ()),
            identity_id="alice",
        ) == ("Reste inconnu — aucun compte emprunteur vérifié.",)
    finally:
        controller.close()


def test_home_ambiguous_selection_never_reads_two_profiles_and_recovers():
    rows = [
        {"identity_id": "alice", "selected": True, "label": "Alice"},
        {"identity_id": "bob", "selected": True, "label": "Bob"},
    ]
    callbacks, queries, renders = [], [], []
    host = NS(
        identity_overview=lambda: rows,
        subscribe_identity_overview=lambda callback: (
            callbacks.append(callback) or (lambda: callbacks.remove(callback))
        ),
    )
    broker = NS(
        identities=lambda: [
            {"identity_id": row["identity_id"], "label": row["label"]}
            for row in rows
        ],
        digital_loan_availability=lambda identity: (
            queries.append(identity) or (capacity(identity),)
        ),
    )
    controller = home.OverviewController(host, renders.append, broker)
    try:
        assert queries == []
        assert all("digital_loan_availability" not in row for row in renders[-1])

        rows[1]["selected"] = False
        callbacks[0]()
        assert queries == ["alice"]
        assert "digital_loan_availability" in renders[-1][0]
        assert "digital_loan_availability" not in renders[-1][1]

        rows[1]["selected"] = True
        callbacks[0]()
        assert queries == ["alice"]  # no stale Alice result retained
        assert all("digital_loan_availability" not in row for row in renders[-1])

        rows[0]["selected"] = False
        callbacks[0]()
        assert queries == ["alice", "bob"]
        assert "digital_loan_availability" not in renders[-1][0]
        assert renders[-1][1]["digital_loan_availability"][0].identity_id == "bob"
    finally:
        controller.close()
    assert callbacks == []


def test_reentrant_profile_handoff_does_not_overwrite_newer_home_projection():
    rows = [
        {"identity_id": "alice", "label": "Alice", "selected": True},
        {"identity_id": "bob", "label": "Bob", "selected": False},
    ]
    callbacks, queries, rendered = [], [], []
    host = NS(
        identity_overview=lambda: list(rows),
        subscribe_identity_overview=lambda cb: (
            callbacks.append(cb) or (lambda: callbacks.remove(cb))
        ),
    )

    def query(identity):
        queries.append(identity)
        if identity == "alice":
            # A broker read may synchronously cause a selected-profile event.
            rows[0]["selected"] = False
            rows[1]["selected"] = True
            callbacks[0]()  # nested newer refresh before the older returns
        return (capacity(identity),)

    broker = NS(
        identities=lambda: [{"identity_id": row["identity_id"]} for row in rows],
        digital_loan_availability=query,
    )
    controller = home.OverviewController(host, rendered.append, broker)
    try:
        assert queries == ["alice", "bob"]
        assert len(rendered) == 1  # only the newest generation may publish
        assert "digital_loan_availability" not in rendered[0][0]
        assert rendered[0][1]["digital_loan_availability"][0].identity_id == "bob"
    finally:
        controller.close()


def test_closing_home_during_broker_read_prevents_dead_render():
    rows = [{"identity_id": "alice", "label": "Alice", "selected": True}]
    callbacks, rendered = [], []
    host = NS(
        identity_overview=lambda: list(rows),
        subscribe_identity_overview=lambda cb: (
            callbacks.append(cb) or (lambda: callbacks.remove(cb))
        ),
    )
    broker = NS(
        identities=lambda: [{"identity_id": "alice"}],
        digital_loan_availability=lambda identity: (capacity(identity),),
    )
    controller = home.OverviewController(host, rendered.append, broker)
    assert len(rendered) == 1
    broker.digital_loan_availability = lambda identity: (
        controller.close() or (capacity(identity),)
    )
    callbacks[0]()
    assert controller.closed
    assert len(rendered) == 1
    assert callbacks == []
