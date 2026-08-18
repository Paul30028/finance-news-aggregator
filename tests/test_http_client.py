"""验证条件请求（ETag/If-Modified-Since）逻辑：内容未变化时应发送条件请求头，
且命中 304 时不应把它当作错误处理。使用 httpx.MockTransport 模拟服务端，
不需要真实网络。
"""
import httpx
import pytest

from app.config import CrawlerConfig, ThrottleConfig
from app.crawler.http_client import CompliantFetcher


def _cfg() -> CrawlerConfig:
    return CrawlerConfig(
        interval_seconds=60,
        max_concurrency=5,
        per_domain_min_interval_seconds=0,  # 测试中不需要真实限速等待
        request_timeout_seconds=5,
        max_retries=1,
        retry_backoff_base_seconds=0.01,
        throttle_on_429_403=ThrottleConfig(),
        user_agent="TestBot/1.0",
        respect_robots_txt=False,  # 跳过 robots.txt 请求，聚焦条件请求逻辑
        robots_cache_ttl_seconds=3600,
        summary_max_length=200,
    )


@pytest.mark.asyncio
async def test_second_request_sends_conditional_headers_and_gets_304():
    call_log = []

    def handler(request: httpx.Request) -> httpx.Response:
        call_log.append(dict(request.headers))
        if request.headers.get("if-none-match") == '"abc123"':
            return httpx.Response(304)
        return httpx.Response(200, content=b"<rss></rss>", headers={"ETag": '"abc123"'})

    fetcher = CompliantFetcher(_cfg())
    fetcher._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "TestBot/1.0"},
    )

    resp1 = await fetcher.fetch("https://example.com/rss.xml")
    assert resp1.status_code == 200

    resp2 = await fetcher.fetch("https://example.com/rss.xml")
    assert resp2.status_code == 304

    # 第二次请求必须带上第一次响应返回的 ETag
    assert call_log[1].get("if-none-match") == '"abc123"'

    await fetcher.aclose()
