"""#6168: duplicate Recent visual keys cannot select another book's detail."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.recent_detail_controller import recent_selected


def test_duplicate_recent_key_never_opens_last_book_detail_or_its_cover():
    displayed = []
    detail = NS(empty=lambda title, message: displayed.append((title, message)))
    window = NS(recent_page=NS(detail=detail), _closed=False)
    # Intentionally no _recent_objects, service, source or provider: the
    # ambiguity must be rejected BEFORE a lookup or a cover fetch can occur.
    row = NS(key="same-placement", _recent_ambiguous_key=True)
    recent_selected(
        window, row, Path=Path,
        format_recent_timestamp=lambda *args, **kwargs: "now",
    )
    assert len(displayed) == 1
    assert "ambiguë" in displayed[0][0]
