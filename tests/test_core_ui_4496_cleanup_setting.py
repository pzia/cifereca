"""#4496: headless GTK control, separate confirmation and lifetime."""
from types import SimpleNamespace as NS

from cifereca.config import Config
from cifereca.core.destructive_cleanup_policy import DESTRUCTIVE_CLEANUP_KEY as KEY
from cifereca.core.destructive_cleanup_settings import CleanupSettingResult
from cifereca.gui import settings_destructive_cleanup as ui
from cifereca.gui.settings_lifetime import SettingsPageLifetime


class Widget:
    def __init__(self, **_kw):
        self.children = []
        self.callbacks = {}
        self.label = ""
        self.sensitive = True
    def set_title(self, _value): pass
    def set_description(self, _value): pass
    def set_wrap(self, _value): pass
    def add(self, child): self.children.append(child)
    def append(self, child): self.children.append(child)
    def set_label(self, label): self.label = label
    def set_sensitive(self, value): self.sensitive = value
    def get_root(self): return None
    def connect(self, name, callback): self.callbacks[name] = callback
    def emit(self, name): self.callbacks[name](self)


class Dialog(Widget):
    def add_response(self, _key, _label): pass
    def set_default_response(self, value): self.default = value
    def set_close_response(self, value): self.close = value
    def present(self): pass
    def respond(self, value): self.callbacks["response"](self, value)


def _build(tmp_path, monkeypatch):
    cfg = Config(tmp_path / "config.json")
    service = NS(config=cfg)
    lifetime = SettingsPageLifetime()
    dialogs, jobs, errors, calls = [], [], [], []

    def dialog(_parent, _title, _body):
        instance = Dialog()
        dialogs.append(instance)
        return instance

    def command(_service, **kwargs):
        calls.append(kwargs)
        cfg.set(KEY, kwargs["enabled"])
        return CleanupSettingResult(kwargs["enabled"], True, True)

    monkeypatch.setattr(ui, "change_destructive_cleanup_setting", command)
    window = NS(service=service, runner=NS(submit=lambda *args, **kwargs: jobs.append(args)),
                show_error=errors.append)
    status, button = ui.build_cleanup_settings(
        window, section=Widget(), page_lifetime=lifetime,
        Gtk=NS(Label=Widget, Button=Widget),
        Adw=NS(PreferencesGroup=Widget, MessageDialog=NS(new=dialog)),
    )
    return cfg, lifetime, status, button, dialogs, jobs, errors, calls


def test_explicit_dialog_and_single_worker_no_action_on_open(tmp_path, monkeypatch):
    cfg, lifetime, status, button, dialogs, jobs, errors, calls = _build(tmp_path, monkeypatch)
    assert not cfg.get(KEY) and jobs == [] and calls == []
    assert "désactivé" in status.label
    button.emit("clicked")
    assert dialogs[0].default == dialogs[0].close == "cancel"
    assert jobs == []
    dialogs[0].respond("cancel")
    assert jobs == [] and button.sensitive
    button.emit("clicked")
    dialogs[1].respond("accept")
    dialogs[1].respond("accept")
    assert len(jobs) == 1 and not button.sensitive
    _, work, done, failed = jobs[0]
    done(work())
    assert calls == [{"expected_config": cfg, "expected_enabled": False,
                      "enabled": True, "confirmed": True}]
    assert "autorisé" in status.label and button.sensitive and not errors


def test_changed_setting_and_disposed_page_refuse_stale_confirmation(tmp_path, monkeypatch):
    cfg, lifetime, status, button, dialogs, jobs, errors, calls = _build(tmp_path, monkeypatch)
    button.emit("clicked")
    cfg.set(KEY, True)
    dialogs[0].respond("accept")
    assert jobs == [] and "autorisé" in status.label
    button.emit("clicked")
    lifetime.dispose()
    dialogs[1].respond("accept")
    assert jobs == [] and calls == []


def test_audit_warning_and_late_callback(tmp_path, monkeypatch):
    cfg, lifetime, status, button, dialogs, jobs, errors, calls = _build(tmp_path, monkeypatch)
    button.emit("clicked")
    dialogs[0].respond("accept")
    _, work, done, failed = jobs[0]
    result = work()
    done(CleanupSettingResult(result.enabled, result.changed, False))
    assert len(errors) == 1 and "journal incomplet" in errors[0]
    button.emit("clicked")
    dialogs[1].respond("accept")
    _, work, done, failed = jobs[1]
    lifetime.dispose()
    failed(RuntimeError("private path"))
    assert len(errors) == 1
