from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("cifereca_ebooksgratuits",ROOT/"cifereca_ebooksgratuits.py")
mod=importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)

HTML="""
<select name="id_auteur"><option value="0">Tous</option><option value="51">Doyle, Arthur Conan</option></select>
<select name="id_categorie"><option value="1">Toutes</option><option value="7">Poésie</option></select>
<select name="id_genre"><option value="1">Tous</option><option value="9">Policier & Mystère</option></select>
"""

class SearchFilterCoverageTests(unittest.TestCase):
    def test_author_choice_is_accent_insensitive(self):
        self.assertEqual(mod._filter_choice(HTML,("id_auteur",),"arthur conan")[0:2],("id_auteur","51"))

    def test_subject_can_match_category_or_genre(self):
        self.assertEqual(mod._filter_choice(HTML,("id_categorie","id_genre"),"mystere")[0:2],("id_genre","9"))
        self.assertEqual(mod._filter_choice(HTML,("id_categorie","id_genre"),"poesie")[0:2],("id_categorie","7"))

    def test_attested_filter_metadata_keeps_genre_and_category_distinct(self):
        self.assertEqual(
            mod._attested_filter_metadata("id_genre","Policier & Mystère"),
            {"genres":["Policier & Mystère"],"genre_evidence":"ebooksgratuits.search_filter"},
        )
        self.assertEqual(
            mod._attested_filter_metadata("id_categorie","Poésie"),
            {"categories":["Poésie"],"category_evidence":"ebooksgratuits.search_filter"},
        )
        self.assertEqual(mod._attested_filter_metadata("id_auteur","Doyle"),{})

    def test_genre_search_projects_only_the_server_selected_genre(self):
        result_html = """
        <ul><li>
        <span class="listeb">Livre test</span><span class="auteur">Auteur</span>
        <a href="details.php?id=1">Détails</a>
        <a href="newsendbook.php?id=1&amp;format=epub">EPUB</a>
        </li></ul>
        """
        response=SimpleNamespace(text=result_html,raise_for_status=lambda:None)
        client=SimpleNamespace(get=lambda *args,**kwargs:response,close=lambda:None)
        session=mod.EbooksSession(
            credential_id="public",label="",credentials={},options={"acquisition_backend":"html"},
            config={},diagnostics=None,transport=None,
        )
        session.connected=True
        session.client=client
        session._fetch=lambda _url:SimpleNamespace(text=HTML)
        rows=session.search("mystere",field="genre")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].metadata["genres"],["Policier & Mystère"])
        self.assertEqual(rows[0].metadata["genre_evidence"],"ebooksgratuits.search_filter")
        self.assertNotIn("categories",rows[0].metadata)

if __name__=="__main__":
    unittest.main()
