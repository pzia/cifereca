from cifereca.book_detail_metadata import detail_metadata_rows, detail_metadata_items


def test_all_known_multivalued_facts_are_kept_separately():
    metadata = {
        "authors": ["A", "B"],
        "subjects": [f"sujet {i}" for i in range(30)],
        "isbn": "9780306406157",
        "edition": "2",
        "catalogue_metadata": {"publishers": ["Éditeur 1", "Éditeur 2"]},
        "epub_metadata": {
            "contributors": [{"name": "C", "role": "illustrator"}],
            "series": [{"name": "Collection test", "index": "3"}],
            "languages": ["fr", "en"],
        },
    }
    rows = detail_metadata_rows(metadata)
    assert ("Auteur", "A") in rows and ("Auteur", "B") in rows
    assert ("Sujet", "sujet 29") in rows
    assert ("ISBN déclaré", "9780306406157") in rows
    assert ("Catalogue · Éditeur", "Éditeur 2") in rows
    assert ("EPUB · Contributeur", "C · illustrator") in rows
    assert ("EPUB · Série", "Collection test · tome 3") in rows
    assert ("EPUB · Langue", "en") in rows


def test_unknown_and_sensitive_payloads_are_not_rendered():
    metadata = {
        "access_token": "secret-value",
        "session": "private",
        "raw_html": "<html>bad</html>",
        "local_path": "/home/user/private.epub",
        "publisher": "https://example.test/?token=secret",
        "subjects": ["public", "Authorization: Bearer secret"],
        "identifiers": {"credential": "nope"},
    }
    assert detail_metadata_rows(metadata) == (("Sujet", "public"),)


def test_duplicates_and_overflow_have_explicit_semantics():
    metadata = {
        "themes": ["fable", "fable"],
        "subjects": [f"thème {i}" for i in range(220)],
    }
    rows = detail_metadata_rows(metadata)
    assert rows.count(("Thème", "fable")) == 1
    assert len(rows) == 201
    assert "21 valeur(s)" in rows[-1][1]


def test_malformed_metadata_is_ignored_without_stringifying_mappings():
    assert detail_metadata_rows(None) == ()
    assert detail_metadata_rows({"authors": [{"password": "private"}]}) == ()
    assert detail_metadata_rows({"collection": {"name": "a"}}) == ()
    assert detail_metadata_rows({"subjects": ["safe", "\x00bad"]}) == (("Sujet", "safe"),)



def test_epub_structured_identifiers_expose_only_attested_public_schemes():
    metadata = {
        "isbn10": "0-306-40615-2",
        "isbn": "9780306406157",
        "epub_metadata": {
            "identifiers": [
                {"scheme": "ISBN", "value": "978-0-306-40615-7", "source": "epub"},
                {"scheme": "DOI", "value": "10.1000/test.123"},
                {"scheme": "EAN", "value": "4006381333931"},
                {"scheme": "KoboAccount", "value": "private-user-id"},
                {"scheme": "ISBN", "value": "not-a-valid-isbn"},
                {"scheme": "ISBN", "value": "https://example.org/?token=private"},
            ]
        }
    }
    fields = detail_metadata_items(metadata)
    assert fields.count(("ISBN déclaré", "9780306406157", "isbn")) == 1
    assert ("EPUB · ISBN déclaré", "9780306406157", "isbn") in fields
    assert ("EPUB · DOI déclaré", "10.1000/test.123", "all") in fields
    assert ("EPUB · EAN déclaré", "4006381333931", "all") in fields
    assert ("EPUB · Identifiant déclaré", "not-a-valid-isbn", "all") in fields
    assert "private-user-id" not in repr(fields)
    assert "token=private" not in repr(fields)


def test_identifiers_mapping_is_explicitly_allowlisted_without_provider_ids():
    fields = detail_metadata_items({
        "identifiers": {
            "isbn": "0-306-40615-2",
            "doi": "10.1234/other",
            "source_item_id": "secret-internal-id",
            "provider": "kobo",
        }
    })
    assert ("ISBN déclaré", "9780306406157", "isbn") in fields
    assert ("DOI déclaré", "10.1234/other", "all") in fields
    assert all("secret-internal-id" not in value for _, value, _ in fields)
    assert all("kobo" != value for _, value, _ in fields)
