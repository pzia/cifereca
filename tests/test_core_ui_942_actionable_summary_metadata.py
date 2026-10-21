"""A field in the concise summary must keep its actionable detail value."""
from cifereca.gui.detail_model import book_detail_presentation


def test_summary_does_not_remove_individual_copy_and_search_targets():
    presentation = book_detail_presentation(
        title='Livre', subtitle='Alice',
        section_rows=(('Essentiel', [('Auteur', 'Alice'), ('Éditeur', 'Maison')]),),
        metadata={'authors': ['Alice', 'Bob'], 'publisher': 'Maison',
                  'series': 'Saga', 'awards': ['Prix public']},
    )
    detail = next(section for section in presentation.sections if section.title == 'Détail')
    fields = [(field.label, field.value, field.kind) for field in detail.fields]
    assert ('Auteur', 'Alice', 'author') in fields
    assert ('Auteur', 'Bob', 'author') in fields
    assert ('Éditeur', 'Maison', 'all') in fields
    assert ('Série', 'Saga', 'series') in fields
    assert ('Prix littéraire', 'Prix public', 'all') in fields
    assert len(fields) == len(set(fields))
