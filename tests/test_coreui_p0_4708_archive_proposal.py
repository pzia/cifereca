"""#4708: read-only archival proposal wording and GTK binding."""
import ast
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/library_inspection_view.py"


def test_archive_proposal_message_never_displays_private_or_forged_values():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == "_archive_proposal_message")
    scope = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<test>", "exec"), scope)
    message = scope["_archive_proposal_message"]
    valid = {"proposal": "supervised_archive_candidate", "reason": "no_dependency_observed",
             "archive_authorized": False, "retirement_authorized": False,
             "requires_fresh_confirmation": True}
    assert "Candidat" in message(valid)
    for value in (None, {}, {**valid, "archive_authorized": True},
                  {**valid, "reason": "PRIVATE_PATH"}, {**valid, "retirement_authorized": True}):
        result = message(value)
        assert result.startswith("Conserver")
        assert "PRIVATE_PATH" not in result
        assert "Aucun archivage ni retrait effectué" in result


def test_archive_button_calls_read_only_session_not_mutation():
    source = SOURCE.read_text(encoding="utf-8")
    ast.parse(source)
    handler = source[source.index("def review_missing_archive(*_args):"):
                     source.index("def select(_listing, row):")]
    assert "session.review_missing_archive(chosen)" in handler
    assert "session.cancel_missing_repair()" in handler
    assert "version != missing_version[0]" in handler
    assert "chosen != missing_case[0]" in handler
    assert "summary.get('status') != 'complete'" in handler
    assert "admit_missing_repair" not in handler
    assert "confirm_missing_repair" not in handler
    assert "MessageDialog" not in handler
    assert "missing_archive_review.connect('clicked', review_missing_archive)" in source
