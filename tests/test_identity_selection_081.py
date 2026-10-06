from cifereca.identity_selection import identity_change_required


def test_same_identity_notification_is_not_a_change():
    assert identity_change_required("alexandre", "alexandre") is False


def test_different_identity_notification_is_a_change():
    assert identity_change_required("default", "alexandre") is True
