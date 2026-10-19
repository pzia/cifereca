"""#1599: only attested catalogue variant records can create ISBN actions.

No GTK, login, acquisition, remote search, or identity mutation.
"""
from types import SimpleNamespace as NS

from recommendation_engine import CatalogNoticeVariant
from recommendations import _attested_catalogue_isbns
from tests.test_recommendation_catalogue_navigation_p1 import _view, _variant

ISBN = "9780306406157"


class ForgedNotice:
    has_source_coordinates = True
    isbn = ISBN
    source_provider = "PRIVATE_TOKEN"
    source_credential = "PRIVATE_ACCOUNT"
    record_id = "PRIVATE_RECORD"


def test_untyped_variant_is_never_an_attested_search_coordinate():
    fake = ForgedNotice()
    assert _attested_catalogue_isbns((fake,)) == ()
    assert _attested_catalogue_isbns((fake, _variant())) == (ISBN,)


def test_forged_and_legitimate_variants_are_isolated_in_public_detail():
    fake = ForgedNotice()
    view, _, calls, detail, _ = _view((fake, _variant()))
    section = next(s for s in detail.sections if s.id == "source-editions")
    editions = [f for f in section.fields
                if f.id.startswith("source-edition-") and f.id != "source-edition-warning"]
    assert len(editions) == 1
    assert "PRIVATE_TOKEN" not in repr((section, detail))
    actions = [action for action in detail.actions
               if action.id.startswith("recommendation.search.isbn.")]
    assert len(actions) == 1
    assert calls == []


def test_only_forged_variant_never_creates_source_sections_or_actions():
    view, _, calls, detail, _ = _view((ForgedNotice(),))
    assert not any(section.id == "source-editions" for section in detail.sections)
    assert not any(action.id.startswith("recommendation.search.isbn.")
                   for action in detail.actions)
    assert calls == []


def test_forged_unbounded_sequence_cannot_materialize_or_attest_isbns():
    class PoisonIterable:
        def __iter__(self):
            raise AssertionError("PRIVATE_TOKEN")
    assert _attested_catalogue_isbns(PoisonIterable()) == ()
    assert _attested_catalogue_isbns({ISBN: ForgedNotice()}) == ()


def test_verified_immutable_variant_remains_local_search_only():
    verified = _variant()
    assert type(verified) is CatalogNoticeVariant
    assert _attested_catalogue_isbns((verified,)) == (ISBN,)
    view, _, calls, detail, _ = _view((verified,))
    action = next(x for x in detail.actions
                  if x.id.startswith("recommendation.search.isbn."))
    assert action.callback() is True
    assert len(calls) == 1
    assert calls[0][1]["online"] is False
