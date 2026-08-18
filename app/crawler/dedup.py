"""
去重模块
--------
基于"标题 + 原文链接"生成稳定哈希，作为文章的唯一指纹（content_hash）。
标题在参与哈希前会做归一化（去空白、转小写），减少因空格/大小写差异导致的重复入库；
链接会去掉常见的追踪参数后再归一化，进一步降低"同一篇文章、不同 UTM 参数"被判定为不同文章的概率。
"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# 常见的营销/统计追踪参数，去重时忽略
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "spm", "from", "ref", "refsrc",
}


def normalize_link(link: str) -> str:
    """去除追踪参数、统一大小写 scheme/host，得到更稳定的链接形式。"""
    parts = urlsplit(link.strip())
    query_pairs = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in _TRACKING_PARAMS
    ]
    normalized_query = urlencode(sorted(query_pairs))
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), netloc, path, normalized_query, ""))


def normalize_title(title: str) -> str:
    """归一化标题：合并空白字符、转小写、去除首尾标点。"""
    text = re.sub(r"\s+", " ", title).strip().lower()
    return text


def compute_content_hash(title: str, link: str) -> str:
    """生成文章去重指纹：sha256(归一化标题 | 归一化链接)。"""
    payload = f"{normalize_title(title)}|{normalize_link(link)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
