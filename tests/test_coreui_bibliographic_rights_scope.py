"""Notice rights prose stays descriptive, source-specific and copy-only."""
import pytest

from cifereca.book_detail_metadata import detail_metadata_items
from cifereca.gui.detail_model import book_detail_presentation


@pytest.mark.parametrize("namespace,prefix", [
    (None, ""), ("catalogue_metadata", "Catalogue · "),
    ("epub_metadata", "EPUB · "),
])
def test_notice_rights_are_explicitly_scoped_and_never_a_search_action(namespace, prefix):
    facts = {"rights": "Tous droits réservés", "license": "Usage personnel"}
    metadata = {namespace: facts} if namespace else facts
    presentation = book_detail_presentation(title="Livre", metadata=metadata)
    fields = [f for s in presentation.sections for f in s.fields]
    assert [(f.label, f.value, f.kind) for f in fields if "notice)" in f.label] == [
        (prefix + "Mention de droits (notice)", "Tous droits réservés", "copy"),
        (prefix + "Licence déclarée (notice)", "Usage personnel", "copy"),
    ]
    assert presentation.actions == ()
    assert presentation.badges == ()


def test_conflicting_source_declarations_remain_separate_without_winning_entitlement():
    rows = detail_metadata_items({
        "rights": "Tous droits réservés",
        "catalogue_metadata": {"rights": "Domaine public"},
        "epub_metadata": {"rights": "Usage personnel"},
        # These are not bibliographic declarations and must never be promoted
        # merely because a public notice advertises a similar word.
        "owned": True, "downloadable": True, "loan_active": True,
    })
    assert rows == (
        ("Mention de droits (notice)", "Tous droits réservés", "copy"),
        ("Catalogue · Mention de droits (notice)", "Domaine public", "copy"),
        ("EPUB · Mention de droits (notice)", "Usage personnel", "copy"),
    )


def test_absent_declaration_does_not_fabricate_unknown_right_or_ownership():
    assert detail_metadata_items({"rights": None, "license": "", "owned": True}) == ()
    assert detail_metadata_items({"public_product_metadata": {
        "rights": "Téléchargeable", "license": "Achat déclaré",
    }}) == ()
