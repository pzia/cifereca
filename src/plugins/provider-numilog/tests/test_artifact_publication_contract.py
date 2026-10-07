from cifereca_numilog_filename import publish


def test_numilog_readable_publish_accepts_identical_existing(tmp_path):
    part = tmp_path / ".numilog.part"
    part.write_bytes(b"identical")
    existing = tmp_path / "Titre - Auteur.epub"
    existing.write_bytes(b"identical")

    result = publish(
        part,
        tmp_path,
        title="Titre",
        author="Auteur",
        book_id="book-1",
    )

    assert result == existing
    assert part.exists()
