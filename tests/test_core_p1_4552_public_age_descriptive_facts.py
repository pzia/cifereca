"""#4552: real admitted public source -> age facts, without inferred joins."""
import pytest

from cifereca.core.notice_source_observation import (
    NoticeSourceKey, admit_source_notice,
)
from cifereca.core.notice_c_public_age_descriptive_facts import (
    public_age_descriptive_facts, admit_public_age_descriptive_facts,
)


def observation(*, visibility="public", age=True):
    key = NoticeSourceKey(
        provider_id="provider.example", visibility=visibility,
        identity_id="identity:1" if visibility == "private" else None,
        credential_id="credential:1" if visibility == "private" else None,
        account_id=None, branch_id="search.public.catalogue",
        record_id="search:abc", edition_id="edition:abc",
        manifestation_role="digital",
    )
    bibliography = {
        "record_id": "record:1", "docbase": "docbase:1",
        "medium": "digital", "revision_id": "rev:1",
    }
    if age:
        bibliography["age_observation"] = {
            "schema": 1, "provider_id": "provider.example",
            "record_id": "record:1", "docbase": "docbase:1",
            "medium": "digital", "source_revision": "rev:1",
            "source_field": "provider.example.age",
            "ranges": [{"age_min": 12, "age_max": 16,
                        "age_kind": "reading", "source_label": "12-16"}],
        }
    return admit_source_notice(
        key=key, parser_id="core.search.public-cover",
        parser_version="2", observed_at=100,
        raw_fields={"search_result": {"public_bibliography": bibliography}},
    )


def test_exact_public_age_is_admitted_without_guessing():
    source = observation()
    facts = public_age_descriptive_facts(source)
    assert len(facts) == 1
    assert facts[0]["edition_id"] == "edition:abc"
    assert '"age_min":12' in facts[0]["value"]
    admitted = admit_public_age_descriptive_facts(
        source, target_edition_id="edition:abc",
        consolidator_id="plugin.age.alpha", consolidator_version="1.0.0",
        attested_strong_links={},
    )
    assert len(admitted.exact) == 1
    assert admitted.inherited == ()


def test_other_edition_requires_attested_strong_link():
    source = observation()
    args = dict(target_edition_id="edition:other",
                consolidator_id="plugin.age.alpha",
                consolidator_version="1.0.0")
    unlinked = admit_public_age_descriptive_facts(
        source, attested_strong_links={}, **args,
    )
    assert not unlinked.exact and not unlinked.inherited
    linked = admit_public_age_descriptive_facts(
        source, attested_strong_links={"edition:abc": "proof:1"}, **args,
    )
    assert len(linked.inherited) == 1
    assert linked.inherited[0].relation_proof_id == "proof:1"


@pytest.mark.parametrize("kwargs", [
    {"visibility": "private"}, {"age": False},
])
def test_private_or_absent_age_never_yields_public_facts(kwargs):
    assert public_age_descriptive_facts(observation(**kwargs)) == ()
