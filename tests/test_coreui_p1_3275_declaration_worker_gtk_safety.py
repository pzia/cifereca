"""#3275: declaration workers must not inspect GTK/identity controls.

The queue runs actual worker functions on a distinct OS thread, then returns
results to the caller's simulated UI loop. No provider/network calls.
"""
from __future__ import annotations

from threading import Thread, get_ident
from types import SimpleNamespace as NS

from cifereca.gui.library_declaration_actions import DeclarationActions
from tests.test_library_declaration_actions import Widget, Dialog


class ThreadRunner:
    def __init__(self):
        self.worker_calls = 0

    def submit(self, title, work, completed, failed, **kwargs):
        outcome = []
        def run():
            try:
                outcome.append(("ok", work()))
            except BaseException as error:
                outcome.append(("error", error))
        task = Thread(target=run)
        task.start()
        task.join(timeout=5)
        assert not task.is_alive(), "worker must finish"
        assert len(outcome) == 1
        self.worker_calls += 1
        state, value = outcome[0]
        if state == "error":
            # Report worker errors to test, not as a harmless preview failure.
            raise value
        completed(value)


def test_declaration_preview_and_confirmation_cancellation_are_gtk_thread_safe():
    ui_thread = get_ident()
    background_callbacks = []
    pair = ("right-b", "file-from-a")
    def require_ui():
        assert get_ident() == ui_thread, "GTK/UI accessor from worker thread"

    def selection():
        require_ui()
        return pair

    def alive():
        require_ui()
        return True

    class Session:
        def valid(self):
            require_ui()
            return True

        def declaration_context(self, right, file):
            require_ui()
            assert (right, file) == pair
            return dict(identity="bob", provider="kobo", account="B-account",
                        edition="edition-1", right=right, path="/library/bob/Book.epub")

        def preview_declared_link(self, right, file, *, revoke, cancelled):
            assert get_ident() != ui_thread
            assert not revoke and (right, file) == pair
            background_callbacks.append("preview")
            assert cancelled() is False
            return {"status": "preview"}

        def confirm_declared_link(self, right, file, *, user_confirmed, cancelled):
            assert get_ident() != ui_thread
            assert user_confirmed is True and (right, file) == pair
            background_callbacks.append("confirm")
            assert cancelled() is False
            return {"status": "declared", "revoke_available": True}

        def revoke_manual_attachment_preview(self):
            raise AssertionError("no cancellation expected")

    Dialog.dialogs = []
    runner = ThreadRunner()
    ui = DeclarationActions(
        Gtk=NS(Expander=Widget, Box=Widget, Label=Widget, Button=Widget,
               Orientation=NS(VERTICAL="vertical")),
        Adw=NS(MessageDialog=Dialog), window=object(),
        runner=runner, session=Session(), selection=selection, alive=alive,
    )
    ui.preview(False)
    assert len(Dialog.dialogs) == 1
    assert "sans preuve fournisseur" in ui.expander.label
    Dialog.dialogs[-1].respond("apply")
    assert background_callbacks == ["preview", "confirm"]
    assert runner.worker_calls == 2
    assert "déclaration utilisateur" in ui.status.label
    assert ui.revoke.visible
