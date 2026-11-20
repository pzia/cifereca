"""Composition must be inert and activation-specific."""
from types import SimpleNamespace

import pytest

from cifereca.core.maintenance_read_factories import maintenance_read_factories
from cifereca.core.activation_scoped_broker import ActivationBrokerResolver


@pytest.mark.parametrize("key,operation", [
    ("library.organizer.read", "preview"),
    ("library.origin.read", "preview"),
    ("library.derived.read", "overview"),
])
def test_read_factories_are_independent_and_revocable(monkeypatch, key, operation):
    from cifereca.core import maintenance_read_factories as module

    instances = []
    sentinel = object()

    class Reader:
        def __init__(self, service):
            assert service is sentinel
            instances.append(self)

        def preview(self, **kwargs):
            return self

        overview = preview

    for name in ("LibraryOrganizerReadBrokerImpl", "LocalOriginBatchReadBrokerImpl",
                 "DerivedMaintenanceReadBrokerImpl"):
        monkeypatch.setattr(module, name, Reader)
    factories = maintenance_read_factories(sentinel)
    assert not instances
    def activation():
        return ActivationBrokerResolver(factories.get, factories.get, "fixture", (key,))
    first, second = activation(), activation()
    a, b = first(key), second(key)
    assert first(key) is a and a is not b
    assert len(instances) == 2
    call = getattr(a, operation)
    assert call() is instances[0]
    assert getattr(b, operation)() is instances[1]
    with pytest.raises(AttributeError):
        getattr(a, "revoke")
    first.close()
    assert call() is None and first(key) is None
    assert getattr(b, operation)() is instances[1]
    second.close()
    assert getattr(b, operation)() is None


def test_undeclared_ports_do_not_construct_readers():
    factories = maintenance_read_factories(SimpleNamespace())
    resolver = ActivationBrokerResolver(factories.get, factories.get, "fixture", ())
    assert all(resolver(key) is None for key in factories)
