"""#2842: private right/file/proof/consent journey uses closed UX labels."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from cifereca.gui.library_guided_steps import guided_stage_text


@pytest.mark.parametrize("phase,step", [
    ("choose_right", 1),
    ("choose_file", 2),
    ("review_pair", 3),
    ("review_ready", 3),
    ("review_blocked", 3),
    ("confirm", 4),
    ("finished", 4),
])
@pytest.mark.parametrize("provider", ["kobo", "numilog"])
def test_guided_steps_are_closed_clear_and_provider_private(phase, step, provider):
    message = guided_stage_text(phase, provider=provider)
    assert message.startswith(f"Étape {step}/4")
    assert not any(word in message for word in (
        "PRIVATE", "account_id", "credential", "/home/", "http://",
    ))
    assert "placement" in message.casefold() or "droit" in message.casefold() or (
        "preuve" in message.casefold()
    )


@pytest.mark.parametrize("phase", [
    "review_pair", "review_ready", "review_blocked", "confirm", "finished",
])
def test_paris_loan_is_read_only_at_every_nonselection_step(phase):
    message = guided_stage_text(phase, provider="paris")
    assert message.startswith("Étape 3/4")
    assert "consultation seule" in message
    assert "Aucun placement" in message


def test_unknown_untrusted_stage_can_never_inject_private_payload():
    private = "PRIVATE_TOKEN /home/account/loans.html"
    assert guided_stage_text(private, provider=private) == (
        guided_stage_text("choose_right")
    )


def test_real_inspection_wires_guidance_to_safe_states_not_private_case_labels():
    path = (Path(__file__).resolve().parents[1] /
            "src/cifereca/gui/library_inspection_view.py")
    source = path.read_text(encoding="utf-8")
    ast.parse(source)
    assert "reconciliation-guided-stage" in source
    assert "from .library_guided_steps import guided_stage_text" in source
    # The status line is updated by the selected-pair state and Core-verified
    # closed result, never formatted with raw case labels or source messages.
    for phase in ("choose_right", "choose_file", "review_pair",
                  "review_ready", "review_blocked", "confirm", "finished"):
        assert re.search(r"['\"]" + phase + r"['\"]", source)
    assert "guided_step.set_text(guided_stage_text(" in source
