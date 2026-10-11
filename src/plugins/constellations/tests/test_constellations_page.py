from types import SimpleNamespace as NS
from cifereca.plugin_api import BookRef
from constellations import normalize_book, ConstellationGraph
from constellation_view_model import node_from_row, merge_nodes, neighbours, Navigation
from constellations_page import ConstellationsPlugin, View


def test_public_book_ref_and_catalog_row_resolve_same_node():
    a=node_from_row(BookRef('42','Title',identifiers={'provider':'bnf','source_item_id':'42'}))
    b=node_from_row({'record_id':'42','provider_id':'bnf','title':'Title'})
    assert a.key==b.key
    assert len(merge_nodes([a,b]))==1


def test_view_delegates_ranking_and_keeps_notice_and_access():
    cards=merge_nodes([node_from_row({'book_id':'a','title':'A','author':'Writer','local_path':'/a'}),
                       node_from_row({'book_id':'b','title':'B','author':'Writer','friendly_url':'https://r.test/b','metadata':{'free':True}})])
    center=next(c for c in cards if c.book.title=='A')
    edge=neighbours(cards,center)[0]
    assert center.availability==('possédé',)
    assert edge.target.availability==('gratuit',)
    assert edge.target.url=='https://r.test/b'
    assert edge.reasons==('Auteur : writer',)


def test_download_url_is_never_a_documentary_link():
    assert not node_from_row({'title':'A','download_url':'https://r.test/download'}).url


def test_valid_isbn10_and_13_deduplicate_without_nameerror():
    cards=merge_nodes([node_from_row({'title':'A','isbn':'0306406152'}),node_from_row({'title':'B','isbn':'9780306406157'})])
    assert len(cards)==1


def test_context_action_does_not_fetch():
    plugin=ConstellationsPlugin().activate(NS())
    book=BookRef('b','Title');calls=[]
    plugin.pages()[0].actions[0].callback(NS(action_context=lambda:NS(book=book),navigate=calls.append))
    assert plugin.pending is book and calls==['constellations']


def test_stale_completion_does_not_cross_identity():
    view=object.__new__(View);view.disposed=False;view.generation=3;view.cache={}
    assert view.loaded(2,'bob',((),(),()),'') is False
    assert view.cache=={}


def test_history_drops_forward_branch():
    history=Navigation()
    for key in ('a','b','c'):history.visit(key)
    assert history.move(-1)=='b'
    history.visit('d')
    assert history.move(1)=='d' and history.history==['a','b','d']


def test_absent_navigation_host_is_graceful():
    messages=[];view=object.__new__(View)
    view.host=NS(toast=messages.append);view.center=node_from_row(BookRef('b','B'))
    view.open_book()
    assert messages


def test_graph_search_is_bounded_even_without_reachable_target():
    graph=ConstellationGraph([{'book_id':str(i),'title':str(i),'authors':['A']} for i in range(15)])
    # A disconnected target used to make DFS explore every simple path.
    assert graph._all_paths('book:0','absent',max_candidates=3)==[]


def test_stable_book_ids_take_precedence_over_title_similarity():
    a=normalize_book({'book_id':'a','title':'Same','authors':['Same']})
    b=normalize_book({'book_id':'b','title':'Same','authors':['Same']})
    assert a.key!=b.key


def test_762_neighbour_cards_keep_main_reason_and_inferred_evidence():
    from constellations_page import neighbour_card_fields, neighbour_count_label
    target = NS(book=BookRef('related', 'Neighbour', ('Writer',)),
                availability=('empruntable',))
    mixed = NS(target=target, reasons=(
        'Inféré : Sujet : nature', 'Auteur : writer', 'Série : recent',
    ))
    certain = neighbour_card_fields(mixed)
    assert certain['title'] == 'Neighbour'
    assert certain['authors'] == 'Writer'
    assert certain['access'] == 'empruntable'
    assert certain['reason'] == 'Auteur : writer'
    assert certain['other_reasons'] == 2
    assert certain['inferred'] is True
    only_inferred = neighbour_card_fields(NS(target=target, reasons=('Inféré : Projet : nature',)))
    assert only_inferred['reason'].startswith('Inféré')
    assert neighbour_count_label((mixed, NS(target=target, reasons=('Auteur : writer',)))).startswith(
        '2 voisin(s) proposé(s) · 1'
    )
    assert neighbour_count_label(()) == 'Aucun voisin proposé avec les paramètres actuels'
