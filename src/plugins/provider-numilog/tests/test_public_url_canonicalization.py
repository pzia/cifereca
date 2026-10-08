from cifereca_numilog import NumilogSession


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


def _session() -> NumilogSession:
    return NumilogSession(
        credential_id="numilog-test",
        label="Numilog",
        credentials={"email": "reader@example.test", "password": "secret"},
        options={},
        config={},
        diagnostics=Diagnostics(),
    )


def _row(public_url):
    return {
        "lid": "12",
        "variant": "()",
        "title": "Livre",
        "cover": None,
        "href": "https://www.numilog.com/Pages/Compte/readBook.ashx?lid=12",
        "public_url": public_url,
    }


def test_canonical_item_preserves_specific_public_url():
    session = _session()
    try:
        public_url = "https://www.numilog.com/livres/notice-12"
        item = session._normalize(_row(public_url))

        session._canonical_item(item)

        assert item.friendly_url == public_url
    finally:
        session.close()


def test_canonical_item_removes_legacy_generic_library_url():
    session = _session()
    try:
        item = session._normalize(_row(None))
        item.friendly_url = session.library_url

        session._canonical_item(item)

        assert item.friendly_url is None
    finally:
        session.close()
