"""#3196: the obsolete Kobo purchase-state projector must not return."""


def test_historical_purchase_states_projector_is_absent():
    from importlib.util import find_spec
    from cifereca.services import LibraryService
    assert find_spec("cifereca.core.search_owned_evidence") is None
    assert not hasattr(LibraryService, "kobo_personal_right_states")
