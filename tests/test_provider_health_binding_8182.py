from cifereca.core.provider_health_projection import PassiveHealth, Scope, Observation, Outcome


def test_reference_binding_unique_and_revoked():
    health = PassiveHealth()
    health.set_enabled(True)
    lease = health.lease()
    a = Scope("fixture", "instance", "a", "owner", "search")
    b = Scope("fixture", "instance", "b", "owner", "search")
    row = Observation(Outcome.AUTH, "1", "now", "ref")
    health.observe(a, row, lease=lease)
    assert health.owns_reference(a, "ref")
    assert not health.owns_reference(b, "ref")
    assert len(health.inventory()) == 1
    health.observe(b, row, lease=lease)
    assert not health.owns_reference(a, "ref")
    assert not health.owns_reference(b, "ref")
    health.set_enabled(False)
    assert not health.owns_reference(a, "ref")
    assert not health.inventory()


def test_archive_guard_serializes_disable():
    from threading import Event, Thread
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "instance", "a", "owner", "search")
    health.observe(scope, Observation(Outcome.AUTH, "1", "now", "ref"), lease=health.lease())
    started, done = Event(), Event()
    def disable():
        started.set()
        health.set_enabled(False)
        done.set()
    with health.archive_guard(scope, "ref") as lease:
        assert lease is not None
        worker = Thread(target=disable)
        worker.start()
        assert started.wait(2)
        assert not done.is_set()
    worker.join(2)
    assert done.is_set()
    with health.archive_guard(scope, "ref") as lease:
        assert lease is None


def test_archive_guard_does_not_block_worker_observation():
    from threading import Event, Thread
    health = PassiveHealth()
    health.set_enabled(True)
    scope = Scope("fixture", "instance", "a", "owner", "search")
    lease = health.lease()
    health.observe(scope, Observation(Outcome.AUTH, "1", "now", "ref"), lease=lease)
    done = Event()
    def worker():
        health.observe(scope, Observation(Outcome.SUCCESS, "1", "later", "new"), lease=lease)
        done.set()
    with health.archive_guard(scope, "ref") as admitted:
        assert admitted == lease
        thread = Thread(target=worker)
        thread.start()
        assert done.wait(2), "projection lock retained during archival"
    thread.join(2)
    assert not thread.is_alive()
