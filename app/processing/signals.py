"""
关键词信号提取模块
------------------
这是一个透明、可解释的规则式分析器：对标题+摘要文本匹配预定义的"信号词库"，
识别常见财经事件类型（降息/加息、业绩超预期/不及预期、评级调整、回购/减持、
并购、监管风险、违约破产、IPO、供给端变化等），并给每个信号打上极性
（利好 +1 / 利空 -1 / 中性 0），加总得到该文章的粗略情绪分。

刻意的设计边界：
  - 这不是情感分析模型，也不做语义理解，只做关键词命中，规则和权重完全透明、
    可审计、可在本文件里直接增删，不存在"黑箱判断"。
  - 输出只是"文章命中了哪些预定义信号词"这一客观事实的结构化整理，
    不构成、也不应被解读为投资建议——具体解读需要结合完整上下文和专业判断。
"""
from __future__ import annotations

from dataclasses import dataclass

# 每个信号规则：(信号代码, 中文标签, 极性, 关键词列表)
# 极性含义：+1 通常被市场解读为利好；-1 通常被解读为利空；0 属于"重大但方向不确定"
# （如并购、IPO、供给端变化——具体利好利空取决于是谁的视角、后续条款等）。
SIGNAL_RULES: list[tuple[str, str, int, list[str]]] = [
    (
        "rate_cut", "货币宽松/降息", 1,
        ["降息", "降准", "减息", "rate cut", "cuts rates", "cuts interest rates", "monetary easing"],
    ),
    (
        "rate_hike", "货币紧缩/加息", -1,
        ["加息", "升息", "rate hike", "raises rates", "raises interest rates", "monetary tightening"],
    ),
    (
        "earnings_beat", "业绩超预期", 1,
        ["业绩超预期", "业绩预增", "净利润增长", "营收增长", "beats estimates", "earnings beat",
         "record profit", "tops forecasts", "better-than-expected"],
    ),
    (
        "earnings_miss", "业绩不及预期", -1,
        ["业绩不及预期", "业绩预警", "业绩下滑", "净利润下降", "净利润亏损", "misses estimates",
         "profit warning", "earnings miss", "posts loss", "worse-than-expected"],
    ),
    (
        "upgrade", "评级/目标价上调", 1,
        ["上调评级", "评级上调", "上调目标价", "upgrades", "raises price target", "buy rating"],
    ),
    (
        "downgrade", "评级/目标价下调", -1,
        ["下调评级", "评级下调", "下调目标价", "downgrades", "cuts price target", "sell rating"],
    ),
    (
        "buyback_dividend", "回购/增持/分红", 1,
        ["回购", "股票回购", "增持", "派息", "分红", "special dividend", "share buyback",
         "announces buyback", "raises dividend"],
    ),
    (
        "insider_selling", "减持/抛售", -1,
        ["减持", "抛售", "清仓", "stake sale", "sells stake", "insider selling"],
    ),
    (
        "mna", "并购重组", 0,
        ["并购", "收购", "合并", "重组", "acquisition", "merger", "to acquire", "takeover bid"],
    ),
    (
        "regulatory_risk", "监管/合规风险", -1,
        ["调查", "处罚", "罚款", "诉讼", "制裁", "investigation", "fined", "lawsuit",
         "sanctions", "sec charges", "antitrust probe"],
    ),
    (
        "default_bankruptcy", "违约/破产风险", -1,
        ["违约", "破产", "债务危机", "default", "bankruptcy", "files for chapter 11", "insolvency"],
    ),
    (
        "ipo_listing", "IPO/新上市", 0,
        ["IPO", "首次公开募股", "申购", "files for ipo", "stock market debut", "goes public"],
    ),
    (
        "supply_shock", "供给端变化", 0,
        ["减产", "断供", "停产", "supply cut", "output cut", "production halt", "opec+"],
    ),
]

_SIGNAL_BY_CODE = {code: (label, polarity) for code, label, polarity, _ in SIGNAL_RULES}


@dataclass
class SignalHit:
    code: str
    label: str
    polarity: int
    matched_keyword: str


def extract_signals(title: str, summary: str) -> list[SignalHit]:
    """在标题+摘要中匹配信号词库，每个信号类型最多返回一次命中（取第一个匹配到的关键词）。"""
    text = f"{title} {summary}"
    hits: list[SignalHit] = []
    for code, label, polarity, keywords in SIGNAL_RULES:
        for kw in keywords:
            if kw and kw.lower() in text.lower():
                hits.append(SignalHit(code=code, label=label, polarity=polarity, matched_keyword=kw))
                break
    return hits


def compute_sentiment_score(hits: list[SignalHit]) -> int:
    """粗略情绪分 = 命中信号极性之和（不是加权模型，只是直观的"利好/利空计数差"）。"""
    return sum(h.polarity for h in hits)


def encode_signal_tags(hits: list[SignalHit]) -> str:
    """把信号列表编码为可存入数据库的逗号分隔字符串，如 'rate_cut,earnings_beat'。"""
    return ",".join(h.code for h in hits)


def decode_signal_tags(tags: str | None) -> list[tuple[str, str, int]]:
    """把存储的信号代码字符串解码回 (code, label, polarity) 列表，供展示使用。"""
    if not tags:
        return []
    result = []
    for code in tags.split(","):
        code = code.strip()
        if code in _SIGNAL_BY_CODE:
            label, polarity = _SIGNAL_BY_CODE[code]
            result.append((code, label, polarity))
    return result
