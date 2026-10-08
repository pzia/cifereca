from __future__ import annotations

from cifereca_numilog import NumilogSession
from cifereca_numilog_html import parse_library_page


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


def test_numilog_library_projects_only_attested_public_book_urls():
    html = """
    <html><body><table id="myTable">
      <div>
        <a href="/livres/notice-12"><span class="pTitle">Titre douze</span></a>
        <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download">Télécharger</a>
      </div>
      <div>
        <a href="/ebooks/notice-34?ref=library"><span class="pTitle">Titre trente-quatre</span></a>
        <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34&amp;m=download">Télécharger</a>
      </div>
      <div>
        <span class="pTitle">Sans notice publique</span>
        <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=56&amp;m=download">Télécharger</a>
      </div>
    </table></body></html>
    """

    rows, next_url = parse_library_page(html)

    assert next_url is None
    assert [row["public_url"] for row in rows] == [
        "https://www.numilog.com/livres/notice-12",
        "https://www.numilog.com/ebooks/notice-34?ref=library",
        None,
    ]

    session = NumilogSession(
        credential_id="numilog-test",
        label="Numilog",
        credentials={},
        options={},
        config={},
        diagnostics=Diagnostics(),
    )
    try:
        items = [session._normalize(row) for row in rows]

        assert [item.friendly_url for item in items] == [
            "https://www.numilog.com/livres/notice-12",
            "https://www.numilog.com/ebooks/notice-34?ref=library",
            None,
        ]
        assert all(item.friendly_url != session.library_url for item in items)
    finally:
        session.close()


def test_numilog_ignores_non_public_or_ambiguous_title_links():
    html = """
    <html><body><table id="myTable">
      <div>
        <a href="/Pages/Compte/private-book.aspx?id=12"><span class="pTitle">Privé</span></a>
        <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download">Télécharger</a>
      </div>
      <div>
        <a href="/livres/a"><span class="pTitle">Ambigu</span></a>
        <a href="/livres/b"><span class="pTitle">Ambigu</span></a>
        <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=34&amp;m=download">Télécharger</a>
      </div>
      <div>
        <a href="https://example.invalid/book"><span class="pTitle">Externe</span></a>
        <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=56&amp;m=download">Télécharger</a>
      </div>
    </table></body></html>
    """

    rows, _ = parse_library_page(html)

    assert [row["public_url"] for row in rows] == [None, None, None]
