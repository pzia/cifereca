"""#6397: source web choice labels must not be rewritten on partial prefixes."""
from types import SimpleNamespace as NS

from cifereca.gui.notice_c_web_action_controller import _web_choice_labels


def test_provider_prefix_is_replaced_only_at_word_boundary():
    core = NS(provider_display_name=lambda provider: "Bibliothèque de Paris")
    choices = (
        NS(provider_id="paris", label="paris · Connexion requise"),
        NS(provider_id="paris", label="parisian · autre source"),
        NS(provider_id="paris", label="paris"),
        NS(provider_id="paris", label="paris (2/3)"),
    )
    assert _web_choice_labels(core, choices) == (
        "Bibliothèque de Paris · Connexion requise",
        "parisian · autre source",
        "Bibliothèque de Paris",
        "Bibliothèque de Paris (2/3)",
    )


def test_duplicate_installed_names_preserve_source_identity():
    core = NS(provider_display_name=lambda provider: "Même bibliothèque")
    choices = (NS(provider_id="a", label="a"), NS(provider_id="b", label="b"))
    assert _web_choice_labels(core, choices) == (
        "Même bibliothèque · a", "Même bibliothèque · b",
    )
