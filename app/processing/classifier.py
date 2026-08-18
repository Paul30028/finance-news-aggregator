"""
关键词分类模块
--------------
非常轻量的规则式分类器：对标题+摘要文本，统计每个分类下关键词的命中次数，
取命中次数最多的分类；若全部为 0，则归类为"其他"。
若源配置了 category_hint，会作为该分类的初始 +0.5 权重（不足以单独决定分类，
但在命中次数打平时可作为 tie-breaker）。

这是一个刻意保持简单的实现：目标是"够用的粗分类"，不是精准的 NLP 分类。
"""
from __future__ import annotations

DEFAULT_CATEGORY = "其他"


def classify(
    title: str,
    summary: str,
    categories: dict[str, list[str]],
    category_hint: str | None = None,
) -> str:
    text = f"{title} {summary}"
    scores: dict[str, float] = {}

    for category, keywords in categories.items():
        hits = sum(1 for kw in keywords if kw and kw in text)
        if hits > 0:
            scores[category] = float(hits)

    if category_hint and category_hint in categories:
        scores[category_hint] = scores.get(category_hint, 0.0) + 0.5

    if not scores:
        return DEFAULT_CATEGORY

    return max(scores.items(), key=lambda kv: kv[1])[0]
