"""#1023: Library matches use local score reasons, without network or rights inference."""
from cifereca.gui.library_rows import project_library_rows


def render(entries, query="", field="all"):
    return project_library_rows(
        entries, row_factory=lambda *args: args,
        provider_display_name=lambda name: name,
        availability_label=lambda row: "Présent" if row.get("local") else "Connu",
        backup_status_label=lambda value: value,
        format_date=lambda value: value,
        query=query, field=field,
    )[0]


def test_library_displays_real_title_and_subject_match_reasons():
    entries = [
        {"key": "k1", "title": "Prix oublié", "author": "",
         "provider_id": "kobo", "local": True,
         "metadata": {"subjects": ["Quai des Orfèvres"]}},
    ]
    rows = render(entries, "prix du quai")
    assert len(rows) == 1
    assert "Présent" in rows[0][3]
    assert "Correspondance : Titre · Sujet" in rows[0][3]
    assert rows[0][4] == "kobo"


def test_library_summary_or_plural_author_only_and_field_strictness():
    entries = [
        {"key": "k1", "title": "Inconnu", "author": None,
         "provider_id": "kobo", "metadata": {
             "authors": ["Claude Martin", "Louise Dupont"],
             "description": "Un lauréat du prix du quai",
         }},
    ]
    assert "Correspondance : Auteur" in render(entries, "claude")[0][3]
    assert "Correspondance : Résumé" in render(entries, "prix quai")[0][3]
    assert "Correspondance" not in render(entries, "prix quai", field="author")[0][3]
    assert "Correspondance" not in render(entries, "")[0][3]


def test_library_unverified_author_fields_are_not_rendered_as_matches():
    entries = [
        {"key": "k1", "title": "Roman", "provider_id": "kobo",
         "metadata": {"authors": [{"secret": "private-token"}],
                      "url": "https://example.invalid/?token=private"}},
    ]
    assert "Correspondance" not in render(entries, "private")[0][3]
