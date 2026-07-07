from app.services.source_candidates import discover_feed_urls_from_html


def test_discover_detects_rss_link():
    html = '<link rel="alternate" type="application/rss+xml" href="/rss.xml">'
    assert discover_feed_urls_from_html(html, "https://example.com/news") == [
        "https://example.com/rss.xml"
    ]


def test_discover_detects_atom_link():
    html = '<link rel="alternate" type="application/atom+xml" href="atom.xml">'
    assert discover_feed_urls_from_html(html, "https://example.com/news/") == [
        "https://example.com/news/atom.xml"
    ]


def test_discover_returns_empty_without_feed_link():
    urls = discover_feed_urls_from_html("<html></html>", "https://example.com")
    assert urls == []


def test_discover_invalid_url_does_not_crash():
    from app.services.source_candidates import discover_feed_urls

    assert discover_feed_urls("not-a-url") == []
