"""#2385: six catalogue modes use the existing canonical C search cursor."""
from unittest.mock import patch

import pytest

from cifereca.core.notice_c_catalogue_query import search_public_catalogue_view
from tests.test_core_p1_2385_catalogue_views import _page


@pytest.mark.parametrize("mode", (
    "list", "grid", "compact", "detail", "sources", "timeline",
))
def test_public_catalogue_query_uses_grouped_authoritative_cursor(mode):
    with patch(
        "cifereca.core.notice_c_catalogue_query.search_public_notice_c",
        return_value=_page(),
    ) as cursor:
        view = search_public_catalogue_view(
            object(), "Book", mode=mode, page=1, page_size=10,
            provider_ids=("provider.example",),
        )
    assert cursor.call_args.kwargs["group_by_document"] is True
    assert cursor.call_args.kwargs["provider_ids"] == ("provider.example",)
    assert cursor.call_args.kwargs["page"] == 1
    assert view.mode == mode
    assert view.has_more and view.status == "partial"
    assert len(view.rows) == (2 if mode == "sources" else 1)


def test_invalid_view_does_not_search():
    with patch(
        "cifereca.core.notice_c_catalogue_query.search_public_notice_c",
    ) as cursor:
        with pytest.raises(ValueError, match="invalid_public_catalogue_view"):
            search_public_catalogue_view(object(), "Book", mode="loans")
        cursor.assert_not_called()
