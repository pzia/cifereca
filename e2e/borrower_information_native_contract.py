"""#4063 native advisory contract, shared by pytest and existing BnF E2E."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT / "src/plugins/provider-bnf-pret/tests",
                  ROOT / "src/plugins/provider-bnf-pret"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def verify_native_advisory(selection_changed):
    """Real GTK widgets and task runner; synthetic HTTPX, no live account."""
    from threading import Event
    from types import SimpleNamespace as NS

    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib, Gtk

    from cifereca.gui.borrower_information_action import add_borrower_information_action
    from cifereca.gui.task_runner import TaskRunner
    from test_p0_2055_borrow_evidence_readonly import EvidenceReadOnlyTests, UNAVAILABLE, RID

    entered, release = Event(), Event()

    def after_detail():
        entered.set()
        if not release.wait(5):
            raise AssertionError("synthetic GTK task was not released")

    session, account, item, calls = EvidenceReadOnlyTests().make(
        detail=UNAVAILABLE, after_detail=after_detail)
    identity = NS(identity_id=account.identity_id)
    connection = {
        "identity_id": account.identity_id, "provider_id": account.provider_id,
        "credential_id": account.credential_id, "enabled": True,
    }
    service = NS(
        _source_context_epoch=0, _account_context_epoch=0, current_identity=identity,
        accounts=[account],
        _sessions={(account.provider_id, account.credential_id): session},
        _session_key=lambda p, c: (p, c),
        _session_for_source=lambda p, c: session,
        _session_for_account=lambda a: session if a is account else None,
        identity_by_id=lambda i: identity if i == identity.identity_id else None,
        _provider_enabled_for_identity=lambda p, i: True,
        storage=NS(provider_connections=lambda **kw: [connection],
                   identity_for_connection=lambda p, c: identity.identity_id),
    )
    Adw.init()
    window = Gtk.Window(default_width=720, default_height=400)
    window.service = service
    window._closed = False
    window._identity_context_generation = 1
    window._account_context_generation = 1
    window._selected_search_id = item.id
    window._search_detail_request_generation = 1
    finished = []
    window._task_started = lambda *args: False
    window._task_finished = lambda *args: finished.append(args) or False
    window.action_button = lambda label, icon: Gtk.Button(label=label)
    window.runner = TaskRunner(window)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    window.set_child(box)
    panel = NS(window=window, _append_content=box.append)

    def wait(predicate):
        loop = GLib.MainLoop()
        ticks = 0

        def tick():
            nonlocal ticks
            ticks += 1
            if predicate() or ticks >= 300:
                loop.quit()
                return False
            return True

        GLib.timeout_add(10, tick)
        loop.run()
        assert predicate(), "native advisory acceptance timed out"

    try:
        assert add_borrower_information_action(panel, item)
        row = next(w for w in walk(box) if isinstance(w, Adw.ActionRow))
        button = next(w for w in walk(row)
                      if isinstance(w, Gtk.Button) and w.get_label() == "Consulter")
        initial = row.get_subtitle()
        window.present()
        wait(button.get_mapped)
        assert calls == []
        assert "non vérifiées" in initial
        button.emit("clicked")
        wait(entered.is_set)
        assert not button.get_sensitive()
        button.emit("clicked")  # even a retained signal cannot submit twice
        if selection_changed:
            window._search_detail_request_generation += 1
        release.set()
        wait(lambda: bool(finished) and button.get_sensitive())
        assert calls == [("GET", "/my_profile/activity"), ("GET", "/resources/" + RID)]
        if selection_changed:
            assert row.get_subtitle() == "Informations non vérifiées."
        else:
            assert "Indisponibilité observée" in row.get_subtitle()
            assert "Permission : inconnue" in row.get_subtitle()
            assert account.unique_id not in row.get_subtitle()
        assert session._borrow_post_attempts == set()
    finally:
        release.set()
        window._closed = True
        window.runner.close()
        window.destroy()
        session.close()
