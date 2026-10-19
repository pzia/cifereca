"""#1599: detached Recommendation GTK pages never refresh or reselect.

Pure headless entrypoint probes; no GTK startup, host broker, network or LLM.
"""
from types import SimpleNamespace

from cifereca.plugin_api import UiBookListEntry
from recommendations import UxRecommendationsView


def inactive():
    # A deactivated page's private GTK objects and service references may
    # already have been destroyed. The entry points must not dereference any.
    instance = object.__new__(UxRecommendationsView)
    instance._disposed = True
    return instance


def test_refresh_after_deactivation_never_starts_tasks_or_mutates_ui():
    view = inactive()
    assert UxRecommendationsView.refresh(view) is False


def test_render_after_deactivation_does_not_access_stale_rankings():
    view = inactive()
    assert UxRecommendationsView._render(view) is None


def test_selected_row_after_deactivation_never_builds_new_actions():
    view = inactive()
    # Old GTK selection signals can still be delivered after widget destroy.
    assert UxRecommendationsView._selected_row(
        view, UiBookListEntry(key="stale", title="Ancien livre")
    ) is None
    assert UxRecommendationsView._selected_row(view, None) is None


def test_progress_after_deactivation_does_not_touch_disposed_widgets():
    view = inactive()
    assert UxRecommendationsView._set_progress(
        view, 7, "Recherche 1/2 · ancien compte"
    ) is False


def test_public_entrypoints_still_operate_when_view_has_not_been_disposed():
    # Existing headless fixtures construct partially populated views via
    # object.__new__. The new guard must remain backward-compatible.
    view = object.__new__(UxRecommendationsView)
    view._disposed = False
    view._generation = 2
    assert UxRecommendationsView._set_progress(view, 1, "Etape") is False
    # Other active rendering/selection behavior is covered by the existing
    # catalogue navigation and source-provenance plugin tests.
