"""Native GTK acceptance of the real Accueil summary (#1867).

Only the public host snapshot is synthetic. The plugin renderer, controller,
widgets and MainWindow allocation are real; no provider/account is contacted.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


def check_home_summary(window, *, later, capture, report, done):
    from gi.repository import Gtk

    path = Path(__file__).resolve().parents[1] / "src/plugins/home-page/cifereca_home.py"
    spec = importlib.util.spec_from_file_location("cifereca_home_acceptance", path)
    home = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(home)

    def walk(widget):
        yield widget
        child = widget.get_first_child()
        while child is not None:
            yield from walk(child)
            child = child.get_next_sibling()

    cases = (
        ("store", ("numilog", "kobo"), 0, 0, 5),
        ("store-public", ("numilog",), 0, None, 5),
        ("public-only", (), 0, None, 0),
        ("loans", ("paris",), 0, 2, 0),
        ("mixed", ("paris", "numilog", "kobo"), 2, 1, 3),
        ("unknown", ("numilog",), 0, None, None),
    )
    snapshot = []
    subscriptions = []
    calls = []

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Rendering Accueil must never trigger an action")

    def subscribe(callback):
        subscriptions.append(callback)
        return lambda: subscriptions.remove(callback)

    def overview():
        calls.append("snapshot")
        return snapshot

    host = SimpleNamespace(
        identity_overview=overview, subscribe_identity_overview=subscribe,
        open_identity_library=forbidden, synchronize=forbidden,
    )
    controllers = []
    page = home.build_page(host, controllers)
    previous = window.view_stack.get_visible_child_name()
    window.view_stack.add_named(page, "acceptance-home-summary")
    window.view_stack.set_visible_child(page)
    index = [0]

    def render_next():
        name, providers, local, loans, owned = cases[index[0]]
        snapshot[:] = [{
            "identity_id": "synthetic-" + name, "label": "Profil témoin " + name,
            "selected": True, "providers": list(providers),
            "sources": (
                [{"provider_id": pid, "connected": True} for pid in providers]
                + ([{"provider_id": "numilog-catalog",
                     "label": "Numilog — Catalogue public",
                     "connected": True}]
                   if name in {"store-public", "public-only"} else [])
            ),
            "statistics": {"local_books": local, "known_loans": loans,
                           "remote_owned_rights": owned},
            "sync": {},
        }]
        assert len(subscriptions) == 1
        subscriptions[0]()
        later(250, verify)

    def verify():
        name, providers, local, loans, owned = cases[index[0]]
        widgets = list(walk(page))
        labels = [node for node in widgets if isinstance(node, Gtk.Label)]
        text = [node.get_text() for node in labels]
        no_lending_source = name in {
            "store", "store-public", "public-only", "unknown",
        }
        loan_label = (
            "aucune source de prêt configurée" if no_lending_source
            else f"prêts de bibliothèque connus : {loans if loans is not None else '—'}"
        )
        expected = (
            f"Livres locaux : {local} · {loan_label}",
            f"Droits d’achat distants : {owned if owned is not None else '—'} "
            "(ne prouvent pas la présence d’un fichier local)",
        )
        allocations = []
        for value in expected:
            matches = [node for node in labels if node.get_text() == value]
            assert len(matches) == 1, (name, value, text)
            node = matches[0]
            assert node.get_mapped() and node.get_width() > 0 and node.get_height() > 0
            ok, bounds = node.compute_bounds(page)
            assert ok and bounds.get_x() >= 0 and bounds.get_y() >= 0
            assert bounds.get_x() + bounds.get_width() <= page.get_width() + 1
            assert bounds.get_y() + bounds.get_height() <= page.get_height() + 1
            assert node.get_layout().get_pixel_size()[0] <= node.get_width() + 1
            allocations.append({"width": node.get_width(), "height": node.get_height()})
        assert not any("emprunts connus" in value for value in text)
        if no_lending_source:
            assert not any(
                "Inventaire des prêts à actualiser" in value
                or "Prêts de bibliothèque non actualisés" in value
                for value in text
            ), (name, text)
            report["checks"]["P1_1867_no_false_loan_alert_" + name] = True
        if "numilog" in providers:
            assert any(value.startswith("Numilog ·") for value in text), text
        if name in {"store-public", "public-only"}:
            public = [
                value for value in text
                if value.startswith("Numilog — Catalogue public ·")
            ]
            assert len(public) == 1, (name, text)
            assert "catalogue public" in public[0]
            assert not any("connexion requise" in value
                           and "Numilog — Catalogue public" in value
                           for value in text)
            report["checks"]["P1_2385_numilog_public_no_fictitious_account_"
                             + name] = True
        report.setdefault("home_summary_allocations", {})[name] = allocations
        report["checks"]["P1_1867_home_real_gtk_" + name] = True
        capture("00-home-summary-" + name)
        index[0] += 1
        if index[0] < len(cases):
            render_next()
            return
        assert len(calls) == len(cases) + 1  # initial build + explicit snapshots
        window.view_stack.set_visible_child_name(previous)
        window.view_stack.remove(page)
        for controller in controllers:
            controller.close()
        assert not subscriptions
        later(250, done)

    render_next()
