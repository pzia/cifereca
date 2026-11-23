"""#3176: invalid broker tokens are refused before scope revalidation."""
import unittest
from unittest.mock import Mock
from cifereca.core.library_organizer_broker import LibraryOrganizerReadBrokerImpl

class OrganizerTokenShapeTests(unittest.TestCase):
    def test_invalid_tokens_fail_without_touching_scope(self):
        broker = LibraryOrganizerReadBrokerImpl(Mock())
        broker._active = (None, None, None, Mock(token="a" * 64))
        for token in ("a" * 63, "a" * 65, "g" * 64, True, None, b"a" * 64):
            with self.subTest(token=repr(token)):
                self.assertIsNone(broker._current(token))

if __name__ == "__main__":
    unittest.main()
