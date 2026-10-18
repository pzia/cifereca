"""The canonical extracted presentation is executable without GTK."""
import os
import subprocess
import sys


def test_detail_projection_without_gtk_keeps_multivalued_metadata_and_redaction():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('neutral detail projection must not load GTK')
sys.meta_path.insert(0, NoGtk())
from cifereca.gui.detail_model import book_detail_presentation
presentation = book_detail_presentation(
    title='Book', group_title='Essentiel', rows=[('Empty', ''), ('Format', 'EPUB')],
    metadata={'authors': ['Alice', 'Bob'], 'subjects': ['nature', 'écologie'],
              'isbn': '9780306406157', 'access_token': 'SECRET_NEVER_DISPLAY',
              'public_product_metadata': {'selected_format_label': 'PDF sans DRM',
                                          'rights': 'SECRET_NEVER_DISPLAY'}},
)
assert presentation.sections[0].title == 'Essentiel'
assert [(f.label, f.value) for f in presentation.sections[0].fields] == [('Format', 'EPUB')]
detail = next(s for s in presentation.sections if s.title == 'Détail')
values = [(f.label, f.value) for f in detail.fields]
assert ('Auteur', 'Alice') in values and ('Auteur', 'Bob') in values
assert ('Sujet', 'nature') in values and ('Sujet', 'écologie') in values
assert ('ISBN déclaré', '9780306406157') in values
assert ('Fiche publique · Format commercial sélectionné', 'PDF sans DRM') in values
assert next(f for f in detail.fields if f.label == 'Fiche publique · Format commercial sélectionné').kind == 'copy'
assert not any('SECRET_NEVER_DISPLAY' in str(v) for _, v in values)
assert 'gi' not in sys.modules and 'cifereca.gui.main_window' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', code], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
