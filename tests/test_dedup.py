from app.crawler.dedup import compute_content_hash, normalize_link, normalize_title


def test_normalize_title_collapses_whitespace_and_case():
    assert normalize_title("  Fed  Raises   Rates ") == "fed raises rates"


def test_normalize_link_strips_tracking_params():
    a = normalize_link("https://Example.com/news/1?utm_source=x&id=1")
    b = normalize_link("https://example.com/news/1?id=1")
    assert a == b


def test_compute_content_hash_is_stable_for_equivalent_articles():
    h1 = compute_content_hash("Fed Raises Rates", "https://example.com/a?utm_source=x")
    h2 = compute_content_hash("  fed   raises rates  ", "https://example.com/a")
    assert h1 == h2


def test_compute_content_hash_differs_for_different_titles():
    h1 = compute_content_hash("Fed Raises Rates", "https://example.com/a")
    h2 = compute_content_hash("Fed Cuts Rates", "https://example.com/a")
    assert h1 != h2
