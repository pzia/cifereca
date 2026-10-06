from cifereca_numilog_html import delivery_url, origin, parse_library_page


def test_local_dataset_library_origin_is_supported():
    html='''<html><body><table id="myTable"><div>
    <img class="imgCouv" src="/covers/12.png"/>
    <a class="LnkDownloadBook" href="/Pages/Compte/readBook.ashx?lid=12&amp;m=download">Télécharger</a>
    <span class="pTitle">Titre local</span>
    </div></table><a rel="next" href="/Pages/Compte/MesLivres.aspx?page=2">Suivant</a></body></html>'''
    rows,next_url=parse_library_page(html,'http://127.0.0.1:8094/Pages/Compte/MesLivres.aspx')
    assert rows[0]['lid']=='12'
    assert rows[0]['cover']=='http://127.0.0.1:8094/covers/12.png'
    assert rows[0]['href'].startswith('http://127.0.0.1:8094/Pages/Compte/readBook.ashx')
    assert next_url=='http://127.0.0.1:8094/Pages/Compte/MesLivres.aspx?page=2'


def test_local_dataset_delivery_origin_is_supported():
    base='http://127.0.0.1:8094/Pages/Compte/readBook.ashx?lid=12'
    target='http://127.0.0.1:8094/Services/Delivery.ashx?lid=12'
    html=f'<html><body><a href="{target}">download</a></body></html>'
    assert delivery_url(html,base,allowed_origins={origin('http://127.0.0.1:8094/')})==target
