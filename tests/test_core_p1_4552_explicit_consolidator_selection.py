"""#4552: no automatic winner, no scope fallback and no rights."""
import pytest

from cifereca.core.notice_consolidator_selection import (
    ConsolidatorDeclaration as D,
    ExplicitSelection, select_consolidator,
)

ONE = D("plugin.age.alpha", "age", "edition:fr", "1.0.0", "schema:age.v1")
TWO = D("plugin.age.beta", "age", "edition:fr", "2.0.0", "schema:age.v2")
GENRE = D("plugin.genre.alpha", "genre", "edition:fr", "1.0.0", "schema:genre.v1")


def resolve(declarations, plugin=None, version=None, **kwargs):
    return select_consolidator(
        declarations, canonical_type=kwargs.get("canonical_type", "age"),
        scope=kwargs.get("scope", "edition:fr"),
        selected_plugin_id=plugin, selected_version=version,
    )


def test_missing_selection_never_implicitly_chooses_unique_candidate():
    assert resolve([ONE]) is None


def test_two_plugins_require_exact_selection_not_first_or_highest_version():
    selected = resolve([TWO, ONE], "plugin.age.alpha", "1.0.0")
    assert selected == ExplicitSelection(
        "age", "edition:fr", "plugin.age.alpha",
        "1.0.0", "schema:age.v1",
    )
    assert resolve([ONE, TWO], "plugin.age.alpha", "1.0.0") == selected
    assert resolve([ONE, TWO], "plugin.age.gamma", "3.0.0") is None


def test_unknown_version_never_substitutes_installed_version():
    assert resolve([ONE], "plugin.age.alpha", "2.0.0") is None


def test_type_and_scope_must_both_match_exactly():
    assert resolve([ONE, GENRE], "plugin.genre.alpha", "1.0.0") is None
    assert resolve([ONE], "plugin.age.alpha", "1.0.0",
                   scope="edition:en") is None


def test_future_canonical_type_does_not_need_core_code_change():
    custom = D("plugin.custom", "library.custom43", "edition:en",
               "1.1.0", "schema:future.1")
    result = resolve([custom], "plugin.custom", "1.1.0",
                     canonical_type="library.custom43", scope="edition:en")
    assert result.schema_id == "schema:future.1"


@pytest.mark.parametrize("declarations", [
    [ONE, ONE],
    [ONE, D("plugin.age.alpha", "age", "edition:fr",
            "1.0.0", "schema:age.bogus")],
])
def test_duplicate_or_conflicting_candidate_cannot_be_accepted(declarations):
    with pytest.raises(ValueError, match="ambiguous_consolidator_declaration"):
        resolve(declarations, "plugin.age.alpha", "1.0.0")


@pytest.mark.parametrize(("plugin", "version"), [
    ("plugin.age.alpha", None),
    (None, "1.0.0"),
    ("plugin.age.alpha", "bad version with whitespace"),
    ("https://plugin?token=x", "1.0.0"),
])
def test_incomplete_or_malformed_config_does_not_select(plugin, version):
    with pytest.raises(ValueError, match="incomplete_consolidator_selection"):
        resolve([ONE], plugin, version)


@pytest.mark.parametrize("declarations", [
    [object()], ["plugin.age.alpha"], [D("", "age", "edition:fr",
                                        "1.0.0", "schema:age.v1")],
    [ONE] * 129,
])
def test_invalid_registry_fails_closed(declarations):
    with pytest.raises(ValueError, match="invalid_consolidator"):
        resolve(declarations, "plugin.age.alpha", "1.0.0")


def test_absence_of_registry_candidates_is_not_error_or_fallback():
    assert resolve([], "plugin.age.alpha", "1.0.0") is None


@pytest.mark.parametrize("declarations", [
    [object()],
    [D("", "age", "edition:fr", "1.0.0", "schema:age.v1")],
    [ONE] * 129,
    [ONE, D("plugin.other", "genre", "", "1.0.0", "schema:genre.v1")],
])
def test_missing_selection_still_validates_entire_registry(declarations):
    """No configured consolidator is not permission to skip admission."""
    with pytest.raises(ValueError, match="invalid_consolidator"):
        resolve(declarations)


def test_missing_selection_with_valid_competing_candidates_is_inert():
    """Competing valid declarations never imply a default winner."""
    assert resolve([ONE, TWO, GENRE]) is None


def test_invalid_unrelated_declaration_rejected_with_explicit_selection():
    bad = D("plugin.other", "genre", "", "1.0.0", "schema:genre.v1")
    with pytest.raises(ValueError, match="invalid_consolidator_declaration"):
        resolve([ONE, bad], "plugin.age.alpha", "1.0.0")
