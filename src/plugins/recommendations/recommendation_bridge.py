from __future__ import annotations

from recommendations import PLUGIN as UI_PLUGIN


class BookReferenceRecommendationBridge:
    """Public recommendation service delegating to the Recommendations UI plugin."""

    def __init__(self) -> None:
        self.context = None

    def activate(self, context):
        self.context = context
        return self

    def deactivate(self) -> None:
        self.context = None

    def recommend_from_book(self, book) -> str:
        return UI_PLUGIN.recommend_from_book(book)


PLUGIN = BookReferenceRecommendationBridge()
