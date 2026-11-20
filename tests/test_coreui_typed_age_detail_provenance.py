"""Native metadata preserves existing typed age declarations, without attestation."""
from cifereca.book_detail_metadata import detail_metadata_fields, detail_metadata_items
from cifereca.gui.detail_model import book_detail_presentation, _essential_fields
from cifereca.plugin_api import DetailField, Provenance, YouthAgeRange


def test_same_age_from_two_declared_sources_keeps_both_fields_and_exact_proofs():
    first = Provenance("éditeur", source_id="edition-a", observed_at="2026-11-01")
    second = Provenance("bibliothèque", source_id="edition-b", observed_at="2026-10-01")
    metadata = {"youth_age_ranges": [
        YouthAgeRange(8, None, "recommended", provenance=first),
        YouthAgeRange(8, None, "recommended", provenance=second),
        YouthAgeRange(8, None, "recommended", provenance=first),
    ]}
    presentation = book_detail_presentation(title="Livre", metadata=metadata)
    fields = [field for section in presentation.sections for field in section.fields
              if field.label == "Âge recommandé"]
    assert len(fields) == 2
    assert [field.value for field in fields] == ["À partir de 8 ans"] * 2
    assert fields[0].provenance is first
    assert fields[1].provenance is second
    assert all(field.kind == "copy" for field in fields)
    assert len({field.id for field in fields}) == 2


def test_typed_and_legacy_age_projection_share_bounds_and_explicit_kind():
    records = [
        YouthAgeRange(None, 6, "interest"),
        YouthAgeRange(8, 12, "reading"),
        YouthAgeRange(9, 9, "publisher"),
        YouthAgeRange(10, None, "inferred"),
        YouthAgeRange(),
        YouthAgeRange(8, 70),
    ]
    legacy = [dict(age_min=r.age_min, age_max=r.age_max, age_kind=r.age_kind)
              for r in records]
    expected = (("Âge d’intérêt", "Jusqu’à 6 ans", "copy"),
                ("Âge de lecture", "De 8 à 12 ans", "copy"),
                ("Âge éditeur", "9 ans", "copy"),
                ("Âge inféré (non attesté)", "À partir de 10 ans", "copy"))
    assert detail_metadata_items({"youth_age_ranges": records}) == expected
    assert detail_metadata_items({"youth_age_ranges": legacy}) == expected


def test_namespace_does_not_create_provenance_and_free_form_mapping_is_not_promoted():
    proof = Provenance("source déclarée", metadata={"private": object()})
    fields = detail_metadata_fields({
        "catalogue_metadata": {"youth_age_ranges": [
            YouthAgeRange(8, 11, "publisher", provenance=proof),
            {"age_min": 8, "age_max": 11, "age_kind": "publisher",
             "provenance": proof, "source_label": "pas une preuve typée"},
        ]},
        "epub_metadata": {"youth_age_ranges": [YouthAgeRange(9, None)]},
    })
    assert [f.label for f in fields] == ["Catalogue · Âge éditeur"] * 2 + ["EPUB · Âge recommandé"]
    assert fields[0].provenance is proof
    assert fields[1].provenance is fields[2].provenance is None


def test_typed_age_collection_keeps_existing_visible_limits():
    fields = detail_metadata_fields({"youth_age_ranges": [
        YouthAgeRange(8, None, provenance=Provenance("éditeur", source_id=str(i)))
        for i in range(620)
    ]})
    assert len(fields) == 201
    assert all(f.provenance is not None for f in fields[:200])
    assert "supplémentaires non affichées" in fields[-1].value
    assert fields[-1].provenance is None


def test_essential_sampling_keeps_an_existing_field_proof_without_guessing():
    proof = Provenance("langue déclarée", source_id="edition")
    field = DetailField("language", "Langue", "Français", "language", provenance=proof)
    sampled = _essential_fields((), (field,))
    assert len(sampled) == 1
    assert sampled[0].provenance is proof
    assert sampled[0].label == field.label
    assert sampled[0].value == field.value


def test_existing_unproven_detail_row_cannot_hide_a_typed_age_declaration():
    proof = Provenance("éditeur", source_id="edition")
    presentation = book_detail_presentation(
        title="Livre", section_rows=(("Détail", (("Âge recommandé", "À partir de 8 ans"),)),),
        metadata={"youth_age_ranges": [YouthAgeRange(8, None, provenance=proof)]},
    )
    fields = [field for section in presentation.sections for field in section.fields
              if field.label == "Âge recommandé"]
    assert len(fields) == 2
    assert fields[0].provenance is None
    assert fields[1].provenance is proof
