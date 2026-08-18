from datetime import datetime, timezone

from app.crawler.rss_parser import RawArticle
from app.processing.cleaner import clean_article, is_blocked, strip_html


def test_strip_html_removes_tags_and_collapses_whitespace():
    assert strip_html("<p>Hello   <b>World</b></p>") == "Hello World"


def test_clean_article_truncates_long_summary():
    raw = RawArticle(
        title="  Some   Title  ",
        link="https://example.com/a",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        raw_summary="<p>" + ("x" * 300) + "</p>",
        source_name="Test Source",
    )
    cleaned = clean_article(raw, summary_max_length=50)
    assert cleaned.title == "Some Title"
    assert len(cleaned.summary) == 53  # 50 chars + "..."
    assert cleaned.summary.endswith("...")


def test_clean_article_falls_back_to_now_when_no_published_time():
    raw = RawArticle(
        title="Title", link="https://example.com/a", published_at=None,
        raw_summary="", source_name="Test",
    )
    cleaned = clean_article(raw)
    assert cleaned.published_at is not None


def test_is_blocked_detects_keyword():
    assert is_blocked("含有博彩广告的新闻", "", ["博彩"]) == "博彩"
    assert is_blocked("正常财经新闻", "摘要内容", ["博彩"]) is None
