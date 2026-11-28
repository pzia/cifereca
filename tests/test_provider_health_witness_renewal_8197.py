from cifereca.core.provider_health_projection import PassiveHealth, Scope, Observation, Outcome

def test_full_witness_set_renews_only_exact_positive_matches():
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "i", "a", "owner", "search")
    lease = health.lease()
    first = Observation(Outcome.SUCCESS, "1", "first", "ref1")
    health.observe(scope, first, lease=lease)
    health.retain_book_witnesses(scope, first, (("db","1"),("db","2"),("db","3")), lease=lease)
    second = Observation(Outcome.SUCCESS, "2", "second", "ref2")
    health.observe(scope, second, lease=lease)
    health.retain_book_witnesses(scope, second, (("db","4"),("db","2")), lease=lease)
    assert health.book_witnesses(scope) == (
        (scope, ("db","1"), "1", "ref1"),
        (scope, ("db","2"), "2", "ref2"),
        (scope, ("db","3"), "1", "ref1"),
    )
