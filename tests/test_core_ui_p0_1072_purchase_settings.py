"""Settings control uses one runner and discards callbacks from dead surfaces."""
from types import SimpleNamespace as NS
from threading import RLock
from unittest.mock import Mock

from cifereca.gui.settings_purchase_enrichment import build_purchase_enrichment_settings
from cifereca.gui.settings_lifetime import SettingsPageLifetime
from cifereca.core.notice_c_purchase_settings import read_purchase_policy
from cifereca.storage import Storage
from tests.test_settings_page_composition import Widget


class Control(Widget):
    def set_wrap(self, value): pass
    def set_title(self, value): self.title = value
    def set_description(self, value): self.description = value
    def add(self, value): self.append(value)


def test_purchase_preference_runner_and_lifetime(tmp_path):
    db = Storage(tmp_path / "settings.sqlite")
    page = SettingsPageLifetime()
    queued = []
    host = NS(service=NS(storage=db, _source_context_lock=RLock()),
              runner=NS(submit=lambda label, work, done, failed, **kw:
                        queued.append((work, done, failed))),
              show_error=Mock())
    section = Control()
    try:
        status, button = build_purchase_enrichment_settings(
            host, section=section, page_lifetime=page,
            Gtk=NS(Label=Control, Button=Control), Adw=NS(PreferencesGroup=Control),
        )
        assert not queued and not button.sensitive
        group, = section.children
        group.emit("map")
        work, done, failed = queued.pop()
        done(work())
        assert button.sensitive and button.label == "Désactiver"
        button.emit("clicked")
        button.emit("clicked")
        assert len(queued) == 1 and read_purchase_policy(db).enabled
        work, done, failed = queued.pop()
        result = work()
        assert not result.enabled
        done(result)
        assert button.label == "Activer" and button.sensitive
        button.emit("clicked")
        work, done, failed = queued.pop()
        page.dispose()
        # Already submitted change can finish, but must not repaint dead GTK.
        done(work())
        assert button.label == "Activer"
        assert read_purchase_policy(db).enabled
        group.emit("map")
        button.emit("clicked")
        assert not queued
        host.show_error.assert_not_called()
    finally:
        db.close()
