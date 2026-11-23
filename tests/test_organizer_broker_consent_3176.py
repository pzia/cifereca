"""#3176: organizer read broker refuses unconfirmed and malformed entrypoints."""
import unittest
from unittest.mock import Mock
from cifereca.core.library_organizer_broker import LibraryOrganizerReadBrokerImpl

class OrganizerBrokerConsentTests(unittest.TestCase):
    def test_preview_without_explicit_confirmation_does_not_touch_service(self):
        service = Mock()
        broker = LibraryOrganizerReadBrokerImpl(service)
        for confirmation in (False, None, 1, "yes"):
            with self.subTest(confirmation=confirmation):
                self.assertIsNone(broker.preview(identity_id="identity", confirmed=confirmation))
        service.assert_not_called()

    def test_non_string_identity_is_rejected(self):
        broker = LibraryOrganizerReadBrokerImpl(Mock())
        for identity in (None, 42, True, b"id"):
            with self.subTest(identity=identity):
                self.assertIsNone(broker.preview(identity_id=identity, confirmed=True))

    def test_next_page_requires_confirmation(self):
        broker = LibraryOrganizerReadBrokerImpl(Mock())
        self.assertIsNone(broker.next_page(token="unknown", confirmed=False))

if __name__ == "__main__":
    unittest.main()
