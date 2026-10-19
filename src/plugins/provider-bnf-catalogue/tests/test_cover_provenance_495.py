import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "cifereca_bnf_catalogue_cover", ROOT / "cifereca_bnf_catalogue.py"
)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


class Diagnostics:
    def add(self, *_args, **_kwargs):
        return None


def test_gallica_document_link_is_not_invented_as_catalog_cover():
    xml = b'''<srw:searchRetrieveResponse
      xmlns:srw="http://www.loc.gov/zing/srw/"
      xmlns:dc="http://purl.org/dc/elements/1.1/">
      <srw:numberOfRecords>1</srw:numberOfRecords>
      <srw:records><srw:record><srw:recordData><dc:dc>
        <dc:identifier>https://catalogue.bnf.fr/ark:/12148/cb100000004d</dc:identifier>
        <dc:title>Titre</dc:title>
        <dc:relation>https://gallica.bnf.fr/ark:/12148/bpt6k123</dc:relation>
      </dc:dc></srw:recordData></srw:record></srw:records>
    </srw:searchRetrieveResponse>'''

    session = mod.BnfCatalogueSession(
        credential_id="public",
        label="",
        credentials={},
        options={},
        diagnostics=Diagnostics(),
    )
    try:
        row = session.parse_records(xml)[0]
    finally:
        session.close()

    assert row.thumbnail_url is None
    assert "catalog_cover" not in row.metadata
    assert row.metadata["document_links"] == [
        {"relation": "gallica", "url": "https://gallica.bnf.fr/ark:/12148/bpt6k123"}
    ]
