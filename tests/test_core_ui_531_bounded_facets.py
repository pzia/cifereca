from cifereca.gui.book_facets import LIST_FACET_MAX_CHARS, compact_facet_label


def test_long_author_is_bounded_but_keeps_readable_prefix():
    value = "Alexandre Dumas père avec un nom volontairement extrêmement long"
    shown = compact_facet_label(value)
    assert len(shown) <= LIST_FACET_MAX_CHARS
    assert shown.endswith("…")
    assert shown.startswith("Alexandre")


def test_many_authors_are_each_bounded_independently():
    authors = [f"Auteur numéro {index} " + ("trèslong " * 8) for index in range(12)]
    shown = [compact_facet_label(value) for value in authors]
    assert len(shown) == 12
    assert all(len(value) <= LIST_FACET_MAX_CHARS for value in shown)


def test_unbroken_word_is_bounded_without_whitespace_dependency():
    value = "Auteur" + "X" * 200
    shown = compact_facet_label(value)
    assert len(shown) == LIST_FACET_MAX_CHARS
    assert shown.endswith("…")


def test_short_facet_is_unchanged():
    assert compact_facet_label("EPUB") == "EPUB"


def test_narrow_layout_contract_keeps_facets_left_aligned_and_wrappable():
    source = open("src/cifereca/gui/book_widgets.py", encoding="utf-8").read()
    assert "self.facet_box.set_halign(Gtk.Align.FILL)" in source
    assert "self.facet_box.set_hexpand(True)" in source
    assert "self.facet_box.set_min_children_per_line(1)" in source
    assert "self.facet_box.set_max_children_per_line(4)" in source
