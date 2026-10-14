"""P0 #1127: real AI settings renderer with headless GTK doubles."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.settings_ai_section import build_ai_settings_section


class Widget:
    def __init__(self, label=None, xalign=None, orientation=None, spacing=None):
        self.label, self.xalign = label, xalign
        self.title = self.subtitle = self.text = self.tooltip = None
        self.active = None
        self.children = []
        self.suffixes = []
        self.classes = []
        self.signals = []
        self.wrap = False

    def set_title(self, text): self.title = text
    def set_subtitle(self, text): self.subtitle = text
    def set_text(self, text): self.text = text
    def set_tooltip_text(self, text): self.tooltip = text
    def set_active(self, value): self.active = value
    def get_active(self): return self.active
    def set_valign(self, *_args): pass
    def add_suffix(self, child): self.suffixes.append(child)
    def add(self, child): self.children.append(child)
    def append(self, child): self.children.append(child)
    def set_wrap(self, value): self.wrap = value
    def connect(self, *args): self.signals.append(args)


def _render(settings=None):
    config = deepcopy(settings if settings is not None else {})
    section = Widget()
    gtk = SimpleNamespace(
        Switch=Widget, Button=Widget, Label=Widget, Box=Widget,
        Align=SimpleNamespace(CENTER="center"),
        Orientation=SimpleNamespace(HORIZONTAL="horizontal"),
    )
    adw = SimpleNamespace(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget)
    style = []
    controls = build_ai_settings_section(
        section=section, settings=config, Gtk=gtk, Adw=adw,
        add_css=lambda widget, *tags: (widget.classes.extend(tags), style.append(tags)),
    )
    return controls, section, config, style


def test_disabled_defaults_original_tree_order_and_no_network():
    ui, section, _, style = _render()
    assert section.children[0].title == "Service IA commun aux plugins"
    group = section.children[0]
    assert [r.title for r in group.children] == [
        "Activer les fonctions d’intelligence artificielle",
        "Protocole pris en charge",
        "Endpoint complet (…/v1/chat/completions)",
        "Modèle installé sur le serveur",
        "Autoriser les requêtes vers un serveur distant",
        "Nom de variable d’environnement pour la clé (facultatif)",
        "Délai maximum en secondes (0,2 à 15)",
        "Taille maximum d’entrée (caractères)",
        "Maximum de jetons de réponse",
    ]
    assert ui.enabled.active is False and ui.remote.active is False
    assert ui.endpoint.text == "" and ui.model.text == ""
    assert ui.key_env.text == ""
    assert ui.timeout.text == "5"
    assert ui.input_limit.text == "4000"
    assert ui.output_limit.text == "256"
    assert section.children[-2].label == "Désactivé"
    assert section.children[-1].children == [ui.test_button, ui.reset_button]
    assert style == [("caption", "dim-label")]


def test_saved_values_are_displayed_without_secret_material():
    settings = {
        "enabled": True, "endpoint": "http://localhost:11434/v1/chat/completions",
        "model": "local-model", "allow_remote": False,
        "api_key_env": "LOCAL_TEST_KEY",
        "timeout_seconds": 2.5, "max_input_chars": 8000,
        "max_output_tokens": 128,
    }
    ui, section, original, _ = _render(settings)
    assert ui.enabled.active is True
    assert ui.remote.active is False
    assert ui.endpoint.text == settings["endpoint"]
    assert ui.model.text == "local-model"
    assert ui.key_env.text == "LOCAL_TEST_KEY"
    assert ui.timeout.text == "2.5"
    assert ui.input_limit.text == "8000"
    assert ui.output_limit.text == "128"
    assert ui.status.label == "Configuré (non testé)"
    assert original == settings


def test_remote_consent_is_not_granted_by_rendering_or_preset():
    ui, _, cfg, _ = _render({
        "enabled": True, "allow_remote": True,
        "endpoint": "https://example.org/v1/chat/completions",
    })
    assert ui.remote.active is True  # Read-only reflection of saved config.
    assert ui.endpoint.text == "https://example.org/v1/chat/completions"
    assert not ui.preset_button.signals
    assert ui.preset_button.label == "Préremplir l’endpoint Ollama local"
    assert cfg["allow_remote"] is True
    assert "Saisir uniquement le NOM de variable" in ui.key_env.tooltip


def test_builder_adds_no_callbacks_and_has_no_service_dependency():
    ui, section, _, _ = _render()
    def walk(widget):
        yield widget
        for child in widget.children + widget.suffixes:
            yield from walk(child)
    assert all(not item.signals for item in walk(section))
    assert ui.test_button.label == "Tester la connexion"
    assert ui.reset_button.label == "Annuler les modifications"


def test_host_keeps_revocation_confirmations_task_runner_and_async_commit():
    source = (Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py").read_text("utf-8")
    rendered = ast.unparse(ast.parse(settings_source_text()))
    ai_path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/settings_ai_actions.py"
    ai_tree = ast.parse(ai_path.read_text("utf-8"))
    callbacks = ast.unparse(next(node for node in ai_tree.body
                                 if isinstance(node, ast.FunctionDef)
                                 and node.name == "bind_settings_ai_callbacks"))
    assert "build_ai_settings_section(" in rendered
    assert "bind_settings_ai_callbacks(" in rendered
    assert "ai_endpoint.connect('notify::text', endpoint_changed)" in callbacks
    assert "ai_remote.set_active(False)" in callbacks
    assert "ai_probe_version[0]" in callbacks
    assert "Adw.MessageDialog.new(" in callbacks
    assert "submit_ai_probe(settings, confirmed=True)" in callbacks
    assert "probe_llm_connection_result(" in callbacks
    assert "self.runner.submit(" in callbacks
    assert "settings_committer.submit(" in rendered
