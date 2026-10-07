from cifereca.plugin_api import publish_artifact_file


def test_public_file_publication_contract_accepts_identical_existing(tmp_path):
    source = tmp_path / "download.part"
    destination = tmp_path / "book.epub"
    source.write_bytes(b"identical")
    destination.write_bytes(b"identical")

    assert publish_artifact_file(source, destination, overwrite=False) == destination
    assert source.exists()
