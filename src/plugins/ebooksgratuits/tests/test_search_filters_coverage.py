from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

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

if __name__=="__main__":
    unittest.main()
