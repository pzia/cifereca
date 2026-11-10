"""#3196: the obsolete Kobo purchase-state projector must not return."""


def test_historical_purchase_states_projector_is_absent():
    from cifereca.core import search_owned_evidence
    from cifereca.services import LibraryService
    assert not hasattr(search_owned_evidence, "scoped_purchase_states")
    assert not hasattr(LibraryService, "kobo_personal_right_states")
