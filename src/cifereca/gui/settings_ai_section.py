"""Presentation-only AI settings widgets for CoreUI P0 #1127.

The MainWindow host owns endpoint consent revocation, synthetic connection
tests, confirmation dialogs, revision/dirty tracking and async persistence.
This builder only creates the existing widget tree: no PyGObject import,
backend probe, secret access, signal registration or service activation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


@dataclass(frozen=True)
class AISettingsWidgets:
    enabled: Any
    endpoint: Any
    model: Any
    remote: Any
    key_env: Any
    timeout: Any
    input_limit: Any
    output_limit: Any
    status: Any
    preset_button: Any
    test_button: Any
    reset_button: Any


def build_ai_settings_section(
    *, section: Any, settings: Mapping[str, Any],
    Gtk: Any, Adw: Any, add_css: Callable[..., Any],
) -> AISettingsWidgets:
    """Build the exact tab rows and buttons, with zero side effects."""
    ai_cfg = settings
    ai_box = section
    ai_group = Adw.PreferencesGroup()
    ai_group.set_title("Service IA commun aux plugins")
    ai_box.append(ai_group)
    ai_enable_row = Adw.ActionRow()
    ai_enable_row.set_title("Activer les fonctions d’intelligence artificielle")
    ai_enable_row.set_subtitle("Désactivé par défaut. Aucun appel réseau à l’ouverture.")
    ai_enabled = Gtk.Switch()
    ai_enabled.set_valign(Gtk.Align.CENTER)
    ai_enabled.set_active(ai_cfg.get("enabled") is True)
    ai_enable_row.add_suffix(ai_enabled)
    ai_group.add(ai_enable_row)

    ai_provider = Adw.ActionRow()
    ai_provider.set_title("Protocole pris en charge")
    ai_provider.set_subtitle("OpenAI Chat Completions compatible (local ou HTTPS)")
    ai_group.add(ai_provider)
    ai_endpoint = Adw.EntryRow()
    ai_endpoint.set_title("Endpoint complet (…/v1/chat/completions)")
    ai_endpoint.set_text(str(ai_cfg.get("endpoint") or ""))
    ai_group.add(ai_endpoint)
    ai_preset = Gtk.Button(label="Préremplir l’endpoint Ollama local")
    ai_preset.set_tooltip_text("N’installe ni ne démarre aucun serveur")
    ai_box.append(ai_preset)
    ai_model = Adw.EntryRow()
    ai_model.set_title("Modèle installé sur le serveur")
    ai_model.set_text(str(ai_cfg.get("model") or ""))
    ai_group.add(ai_model)

    ai_remote_row = Adw.ActionRow()
    ai_remote_row.set_title("Autoriser les requêtes vers un serveur distant")
    ai_remote_row.set_subtitle(
        "HTTPS obligatoire ; les plugins demandent toujours leur propre consentement."
    )
    ai_remote = Gtk.Switch()
    ai_remote.set_valign(Gtk.Align.CENTER)
    ai_remote.set_active(ai_cfg.get("allow_remote") is True)
    ai_remote_row.add_suffix(ai_remote)
    ai_group.add(ai_remote_row)

    ai_key = Adw.EntryRow()
    ai_key.set_title("Nom de variable d’environnement pour la clé (facultatif)")
    ai_key.set_text(str(ai_cfg.get("api_key_env") or ""))
    ai_key.set_tooltip_text("Saisir uniquement le NOM de variable, jamais la clé")
    ai_group.add(ai_key)
    ai_timeout = Adw.EntryRow()
    ai_timeout.set_title("Délai maximum en secondes (0,2 à 15)")
    ai_timeout.set_text(str(ai_cfg.get("timeout_seconds", 5)))
    ai_group.add(ai_timeout)
    ai_input_limit = Adw.EntryRow()
    ai_input_limit.set_title("Taille maximum d’entrée (caractères)")
    ai_input_limit.set_text(str(ai_cfg.get("max_input_chars", 4000)))
    ai_group.add(ai_input_limit)
    ai_output_limit = Adw.EntryRow()
    ai_output_limit.set_title("Maximum de jetons de réponse")
    ai_output_limit.set_text(str(ai_cfg.get("max_output_tokens", 256)))
    ai_group.add(ai_output_limit)

    ai_notice = Gtk.Label(
        label=("La clé n’est jamais enregistrée : seul son nom de variable est sauvegardé. "
               "Une analyse peut coûter de l’argent chez un fournisseur distant. "
               "Sauvegarder la configuration requiert un redémarrage de Cifereca."),
        xalign=0,
    )
    ai_notice.set_wrap(True)
    add_css(ai_notice, "caption", "dim-label")
    ai_box.append(ai_notice)
    ai_status = Gtk.Label(label="Désactivé" if not ai_enabled.get_active() else "Configuré (non testé)", xalign=0)
    ai_status.set_wrap(True)
    ai_box.append(ai_status)
    ai_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    ai_test = Gtk.Button(label="Tester la connexion")
    ai_reset = Gtk.Button(label="Annuler les modifications")
    ai_buttons.append(ai_test)
    ai_buttons.append(ai_reset)
    ai_box.append(ai_buttons)

    return AISettingsWidgets(
        enabled=ai_enabled, endpoint=ai_endpoint, model=ai_model,
        remote=ai_remote, key_env=ai_key, timeout=ai_timeout,
        input_limit=ai_input_limit, output_limit=ai_output_limit,
        status=ai_status, preset_button=ai_preset,
        test_button=ai_test, reset_button=ai_reset,
    )
