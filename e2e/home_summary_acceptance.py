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
        ("catalog", ("numilog-catalog",), 0, None, None),
        ("loans", ("paris",), 0, 2, 0),
        ("mixed", ("paris", "numilog", "kobo", "numilog-catalog"), 2, 1, 3),
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
            "sources": [{"provider_id": pid, "connected": True} for pid in providers],
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
        no_lending_source = name in {"store", "catalog", "unknown"}
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
            assert any(value.startswith("Numilog — Mes achats (compte) ·")
                       for value in text), text
        if "numilog-catalog" in providers:
            assert any(value.startswith("Numilog — Catalogue public ·")
                       and "sans achat attesté" in value for value in text), text
            # Anonymous offers cannot imply credentials or ownership.
            assert not any("Numilog — Catalogue public" in value
                           and ("connectée" in value or "connexion requise" in value)
                           for value in text), text
            report["checks"]["P1_1867_public_numilog_not_owned_" + name] = True
        report.setdefault("home_summary_allocations", {})[name] = allocations
        report["checks"]["P1_1867_home_real_gtk_" + name] = True
        capture("00-home-summary-" + name)
        index[0] += 1
        if index[0] < len(cases):
            render_next()
            return
        assert len(calls) == len(cases) + 1  # initial build + explicit snapshots
        native_store_filters()

    def native_store_filters():
        """Render the real GTK source selector with synthetic provider facts.

        This reuses the production widget and source projection, without
        installing, connecting, enabling or querying any provider.
        """
        from cifereca.gui.query_controls import QueryControls
        from cifereca.gui.query_widget_factory_controller import _provider_choices

        live = [
            ("kobo", "Kobo"), ("numilog", "Numilog"),
            ("numilog-catalog", "Numilog — Catalogue public"),
        ]
        source = SimpleNamespace(service=SimpleNamespace(
            searchable_providers=lambda: list(live),
        ))
        choices = _provider_choices(source)
        assert set(dict(choices)) == {"kobo", "numilog", "numilog-catalog", "local"}
        controls = QueryControls(choices, lambda: None, lambda: None, library=False)
        outer = page.get_child()
        outer.prepend(controls)
        controls.provider.set_values(("numilog-catalog",))

        def verify_native():
            assert controls.get_mapped() and controls.get_width() > 0
            assert controls.get_width() <= page.get_width() + 1
            options = controls.provider.buttons
            assert options["numilog"].get_label() == "Numilog — Mes achats (compte)"
            assert options["numilog-catalog"].get_label() == "Numilog — Catalogue public"
            assert options["kobo"].get_label() == "Kobo — Catalogue et compte"
            assert controls.state().providers == ("numilog-catalog",)
            capture("00-store-source-filter-public")

            # Switching identity or disabling a public source is represented
            # by its absence from Core discovery, not by UI permission guesses.
            live[:] = [("kobo", "Kobo"), ("numilog", "Numilog")]
            controls.set_provider_choices(_provider_choices(source))
            assert "numilog-catalog" not in controls.provider.buttons
            assert controls.state().providers == ()
            controls.provider.set_values(("numilog",))
            assert controls.state().providers == ("numilog",)
            live.append(("numilog-catalog", "Numilog — Catalogue public"))
            controls.set_provider_choices(_provider_choices(source))
            assert controls.state().providers == ("numilog",)
            assert not controls.provider.buttons["numilog-catalog"].get_active()
            report["checks"]["P1_1867_native_store_filter_roles_and_opt_in"] = True
            report["store_source_filter_width"] = controls.get_width()

            outer.remove(controls)
            window.view_stack.set_visible_child_name(previous)
            window.view_stack.remove(page)
            for controller in controllers:
                controller.close()
            assert not subscriptions
            later(250, done)

        later(250, verify_native)

    render_next()
