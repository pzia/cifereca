"""Identity-safe recommendation action orchestration (#1129)."""
from __future__ import annotations


def add_book_recommendation_action(window, detail, item) -> None:
    if getattr(window, '_closed', False):
        return
    if window._book_recommendation_target(item) is None:
        return
    button = window.action_button(
        "Recommander à partir de ce livre",
        "starred-symbolic",
    )
    button._cifereca_action_id = "recommend-from-book"
    button._cifereca_action_priority = 25
    context = (
        getattr(window, "_identity_context_generation", None),
        getattr(window, "_account_context_generation", None),
    )

    def current():
        return (
            not getattr(window, "_closed", False)
            and context == (
                getattr(window, "_identity_context_generation", None),
                getattr(window, "_account_context_generation", None),
            )
        )

    def recommend(_button):
        if not current():
            return
        target = window._book_recommendation_target(item)
        if target is None:
            window.toast("Le service de recommandation n’est plus disponible.")
            return
        service, book = target
        try:
            route = str(service.recommend_from_book(book) or "").strip()
        except Exception as exc:
            if current():
                window.show_error(str(exc), "Recommandation impossible")
            return
        if not current():
            return
        if not route:
            window.show_error(
                "Le service n’a fourni aucune page à ouvrir.",
                "Recommandation impossible",
            )
            return
        window.navigate(route)

    button.connect("clicked", recommend)
    detail.add_actions([button])
