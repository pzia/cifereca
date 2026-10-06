from cifereca_numilog_html import parse_library_page


def test_library_page_returns_validated_next_link():
    html='''<html><body><table id="myTable"><div><a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download">Télécharger</a><span class="pTitle">Titre</span></div></table><a rel="next" href="/Pages/Compte/MesLivres.aspx?page=2">Suivant</a></body></html>'''
    rows,next_url=parse_library_page(html)
    assert rows[0]['lid']=='12'
    assert next_url=='https://www.numilog.com/Pages/Compte/MesLivres.aspx?page=2'
