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


def test_youth_age_ranges_are_projected_only_with_attested_kind_and_bounds():
    rows = detail_metadata_items({
        "youth_age_ranges": [
            {"age_min": 8, "age_max": 11, "age_kind": "publisher",
             "source_label": "PRIVATE_FAMILY_ACCOUNT",
             "source_id": "secret-library"},
            {"age_min": 8, "age_max": 11, "age_kind": "reading"},
            {"age_min": 13, "age_max": None, "age_kind": "interest"},
            {"age_min": 10, "age_max": None, "age_kind": "inferred"},
        ],
    })
    assert ("Âge éditeur", "De 8 à 11 ans", "copy") in rows
    assert ("Âge de lecture", "De 8 à 11 ans", "copy") in rows
    assert ("Âge d’intérêt", "À partir de 13 ans", "copy") in rows
    assert ("Âge inféré (non attesté)", "À partir de 10 ans", "copy") in rows
    assert "PRIVATE_FAMILY_ACCOUNT" not in repr(rows)
    assert "secret-library" not in repr(rows)


def test_invalid_youth_age_bounds_cannot_be_displayed_as_publisher_attestations():
    rows = detail_metadata_items({
        "youth_age_ranges": [
            {"age_min": True, "age_max": 12, "age_kind": "publisher"},
            {"age_min": "13", "age_max": None, "age_kind": "publisher"},
            {"age_min": -1, "age_max": 12, "age_kind": "publisher"},
            {"age_min": 13, "age_max": 8, "age_kind": "publisher"},
            {"age_min": None, "age_max": None, "age_kind": "publisher"},
            {"age_min": 8, "age_max": 70, "age_kind": "publisher"},
            {"age_min": 8, "age_max": 11, "age_kind": "PRIVATE_SOURCE"},
            {"age_min": 8, "age_max": 11,
             "age_kind": "publisher", "source_label": "Bearer PRIVATE"},
            {"age_min": None, "age_max": 6, "age_kind": "interest"},
            {"age_min": 15, "age_max": 18, "age_kind": "publisher"},
        ],
        "token": "Bearer PRIVATE",
        "local_path": "/home/private/reader",
    })
    assert rows == (
        ("Âge éditeur", "De 8 à 11 ans", "copy"),
        ("Âge d’intérêt", "Jusqu’à 6 ans", "copy"),
        ("Âge éditeur", "De 15 à 18 ans", "copy"),
    )
    assert "Bearer PRIVATE" not in repr(rows)
    assert "/home/private" not in repr(rows)


def test_youth_age_projection_remains_bounded_and_preserves_provenance_namespace():
    repeated = [{"age_min": i % 12, "age_max": 16, "age_kind": "publisher"}
                for i in range(620)]
    rows = detail_metadata_items({
        "youth_age_ranges": repeated,
        "catalogue_metadata": {
            "youth_age_ranges": [{"age_min": 7, "age_max": 9, "age_kind": "reading"}],
        },
    })
    assert len(rows) <= 201
    assert ("Catalogue · Âge de lecture", "De 7 à 9 ans", "copy") in rows
    crowded = detail_metadata_items({"subjects": [f"sujet-{i}" for i in range(220)]})
    assert len(crowded) == 201
    assert any("valeur(s) supplémentaires non affichées" in value
               for _, value, _ in crowded)



def test_commercial_product_labels_have_a_separate_copy_only_namespace():
    rows = detail_metadata_items({
        "format": "epub", "page_count": 200,
        "public_product_metadata": {
            "brand": "Maison publique", "collection": "Collection publique",
            "publication_date": "2026-10-01", "pagination_label": "228 pages",
            "language_label": "Français", "selected_format_label": "PDF sans DRM",
            "rights": "downloadable", "format": "pdf", "isbn": "9780306406157",
            "identifiers": {"isbn":"9780306406157"},
            "youth_age_ranges": [{"age_kind":"publisher", "age_min":8}],
            "source_url": "https://example.test/?token=PRIVATE",
            "private_account": "PRIVATE",
        },
    })
    assert ("Format", "epub", "all") in rows
    assert ("Pages", "200", "copy") in rows
    public = [r for r in rows if r[0].startswith("Fiche publique · ")]
    assert len(public) == 6
    assert ("Fiche publique · Pagination déclarée (support non précisé)", "228 pages", "copy") in public
    assert ("Fiche publique · Format commercial sélectionné", "PDF sans DRM", "copy") in public
    assert all(kind == "copy" for _, _, kind in public)
    assert "PRIVATE" not in repr(rows) and "downloadable" not in repr(rows)
    assert "9780306406157" not in repr(rows)


def test_commercial_namespace_does_not_stringify_internal_structures_or_urls():
    assert detail_metadata_items({"public_product_metadata": {
        "brand": {"secret":"PRIVATE"},
        "selected_format_label": "https://example.test/?auth=PRIVATE",
        "pagination_label": "\x00PRIVATE",
        "language_label": ["Français", "Français"],
    }}) == (("Fiche publique · Langue déclarée", "Français", "copy"),)


def test_commercial_namespace_preserves_existing_limits_and_duplicate_semantics():
    rows = detail_metadata_items({"public_product_metadata": {
        "brand": [str(i) for i in range(230)],
    }})
    assert len(rows) == 201 and "30 valeur(s)" in rows[-1][1]
    assert detail_metadata_items({"brand":"No new root key"}) == ()


def test_indexed_editorial_facts_are_also_visible_and_individually_actionable():
    rows = detail_metadata_items({
        'awards': ['Prix du Quai', 'Prix public'], 'prizes': ['Prix du Quai'],
        'literary_prizes': ['Prix des lecteurs'],
        'keywords': ['enquête', 'roman'], 'tags': ['roman'],
        'collections': ['Policier', 'Poche'], 'series_index': 2,
        'collection_index': 12, 'edition_statement': 'Édition revue',
        'original_language': 'en', 'genre': 'policier', 'categories': ['Romans'],
        'catalogue_metadata': {'awards': ['Prix catalogue']},
        'epub_metadata': {'keywords': ['enquête locale']},
        'public_product_metadata': {'awards': ['Non attesté pour cette édition']},
    })
    assert rows.count(('Prix littéraire', 'Prix du Quai', 'all')) == 1
    assert ('Prix littéraire', 'Prix des lecteurs', 'all') in rows
    assert rows.count(('Mot-clé', 'roman', 'subject')) == 1
    assert ('Collection', 'Poche', 'collection') in rows
    assert ('Numéro dans la série', '2', 'copy') in rows
    assert ('Numéro dans la collection', '12', 'copy') in rows
    assert ('Édition', 'Édition revue', 'all') in rows
    assert ('Langue originale', 'en', 'language') in rows
    assert ('Catalogue · Prix littéraire', 'Prix catalogue', 'all') in rows
    assert ('EPUB · Mot-clé', 'enquête locale', 'subject') in rows
    assert 'Non attesté' not in repr(rows)


def test_editorial_extensions_keep_the_existing_public_text_boundary():
    rows = detail_metadata_items({
        'awards': [{'secret': 'PRIVATE'}, 'https://example.test/?token=PRIVATE'],
        'keywords': ['Enquête', 'Authorization: Bearer PRIVATE'],
        'collections': [{'name': 'Unknown structure', 'account': 'PRIVATE'}],
        'tags': ['\x00PRIVATE'], 'literary_prizes': ['Prix public'],
    })
    assert rows == (('Mot-clé', 'Enquête', 'subject'),
                    ('Prix littéraire', 'Prix public', 'all'))


def test_summaries_keep_paragraphs_complete_copy_and_source_differences():
    text = "Premier paragraphe.\n\nSecond paragraphe."
    rows = detail_metadata_items({
        'description': '<p>Premier paragraphe.</p><p>Second paragraphe.</p>',
        'summary': text,
        'synopsis': '<script>PRIVATE</script><p>Résumé différent.</p>',
        'epub_metadata': {'description': 'Version EPUB attestée.'},
    })
    assert rows.count(('Résumé', text, 'copy')) == 1
    assert ('Résumé', 'Résumé différent.', 'copy') in rows
    assert ('EPUB · Résumé', 'Version EPUB attestée.', 'copy') in rows
    assert 'PRIVATE' not in repr(rows) and '<p>' not in repr(rows)
    assert detail_metadata_items({'summary': 'https://example.test/?token=PRIVATE'}) == ()


def test_declared_identifiers_do_not_extract_isbn_from_unrelated_references():
    from cifereca.book_detail_metadata import canonical_book_isbn
    for value in ('SKU9780306406157', '9780306406157suffix',
                  '9780306406157 / 9780306406157', '<b>9780306406157</b>'):
        assert canonical_book_isbn(value) == ''
        rows = detail_metadata_items({'isbn': value})
        assert rows and all(kind != 'isbn' for _, _, kind in rows)
    assert canonical_book_isbn('978 0 306 40615 7') == '9780306406157'


def test_sensitive_assignments_and_addresses_never_reach_detail_actions():
    from cifereca.book_detail_metadata import safe_detail_value
    for value in ('token = PRIVATE', 'cookie : PRIVATE', 'password = PRIVATE',
                  'Basic cHJpdmF0ZQ==', 'Compte <private@example.test>'):
        assert safe_detail_value(value) == ''
        assert detail_metadata_items({'keywords': [value], 'awards': [value]}) == ()
    assert safe_detail_value('Le Secret de la maison') == 'Le Secret de la maison'
