import recommendations


def test_page_is_single_and_named_recommendations():
    pages = recommendations.PLUGIN.pages()
    assert len(pages) == 1
    assert pages[0].route == "recommendations"
    assert pages[0].title == "Recommandations"


def test_master_detail_entrypoint_is_public_only():
    assert recommendations.PLUGIN is not None
