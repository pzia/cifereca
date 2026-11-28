from cifereca.core.provider_health_replay import compare_business_projection

def test_comparison_is_order_independent_and_reports_changes():
    a = ("db", "1", "Title", "Author")
    b = ("db", "2", "Other", "Author")
    c = ("db", "1", "Changed", "Author")
    compare = lambda x, y: compare_business_projection(x, y, baseline_version="1", replay_version="2")
    assert compare((a,b), (b,a))["outcome"] == "equal"
    assert compare((a,b), (c,)) == {"outcome":"different","added":0,"removed":1,"changed":1,"version_changed":True}
    assert compare((a,a), (a,))["outcome"] == "unknown"
    assert compare((a,), ())["outcome"] == "unknown"
