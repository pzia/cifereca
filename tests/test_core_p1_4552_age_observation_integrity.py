"""#4552: reject forged and mutated source observations before fact projection."""
from dataclasses import replace

from cifereca.core.notice_c_public_age_descriptive_facts import public_age_descriptive_facts
from tests.test_core_p1_4552_public_age_descriptive_facts import observation


def test_forged_revision_never_admitted():
    source = observation()
    assert public_age_descriptive_facts(
        replace(source, source_revision="notice-raw.v1:" + "0" * 64)
    ) == ()


def test_changed_timestamp_does_not_change_source_revision():
    source = observation()
    assert public_age_descriptive_facts(
        replace(source, observed_at=source.observed_at + 1)
    ) == public_age_descriptive_facts(source)


def test_mutated_raw_json_never_admitted():
    source = observation()
    assert public_age_descriptive_facts(
        replace(source, raw_json=source.raw_json + " ")
    ) == ()
