"""
关键词信号提取模块
------------------
这是一个透明、可解释的规则式分析器：对标题+摘要文本匹配预定义的"信号词库"，
识别常见财经事件类型（降息/加息、业绩超预期/不及预期、评级调整、回购/减持、
并购、监管风险、违约破产、IPO、供给端变化等），并给每个信号打上极性
（利好 +1 / 利空 -1 / 中性 0），加总得到该文章的粗略情绪分。

除了信号本身，本模块还维护一份 ACTION_HINTS（每类信号对应"建议关注的后续信息"），
用于把"检测到了什么"进一步整理成"接下来该看什么"，让分析结果更"可行动"——但这里的
"行动"始终是"去核实更多信息"，而不是任何形式的买卖指令。

刻意的设计边界：
  - 这不是情感分析模型，也不做语义理解，只做关键词命中，规则和权重完全透明、
    可审计、可在本文件里直接增删，不存在"黑箱判断"。
  - 输出只是"文章命中了哪些预定义信号词，以及这类事件通常还需要核实什么"，
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

# 每类信号对应的"建议关注的后续信息"——不是操作指令，而是"这类事件通常还需要
# 看什么才能形成更完整的判断"，帮助从"看到一条新闻"过渡到"知道下一步该查什么"。
ACTION_HINTS: dict[str, str] = {
    "rate_cut": "后续关注同期 CPI/PMI 等数据是否印证宽松基调，以及权益/债券市场的实际反应",
    "rate_hike": "后续关注该经济体通胀数据走势，以及后续会议是否释放进一步紧缩信号",
    "earnings_beat": "关注管理层对下季度的指引，以及同业公司是否有相似趋势",
    "earnings_miss": "关注公司给出的下滑原因说明，以及是否为行业性而非个案问题",
    "upgrade": "关注该机构给出的具体理由与目标价，并与其他机构观点对照",
    "downgrade": "关注下调理由是基本面恶化还是估值调整，避免只看结论不看依据",
    "buyback_dividend": "关注回购/分红的具体规模与资金来源，判断是否反映管理层信心",
    "insider_selling": "关注减持方是否为控股股东/高管，及减持原因说明（如有）",
    "mna": "关注交易对价、支付方式、监管审批进度，交易能否完成存在不确定性",
    "regulatory_risk": "关注涉事金额/范围、公司回应，以及是否会升级为更严重的合规后果",
    "default_bankruptcy": "关注债务规模、债权人反应及后续重组方案",
    "ipo_listing": "关注发行估值、募资用途及基石投资者构成",
    "supply_shock": "关注影响持续时间，以及下游相关行业的连锁反应",
}

_ALERT_CONFIDENCE_LABELS = {
    0: "",
    1: "单一信号，建议结合更多信息交叉验证",
    2: "双重信号叠加，关注度提升",
}
_ALERT_CONFIDENCE_LABEL_MAX = "多重信号叠加，建议优先关注"


def conclusion_for_codes(codes: list[str]) -> tuple[str, str]:
    """根据命中的信号代码，生成 (关注建议, 信号强度描述) 二元组，供展示层使用。

    "信号强度"指的是同一篇文章命中了几种不同的预定义信号类型（越多说明多个规则
    同时给出一致或相关的提示），不是对事件重要性或后续走势的预测。
    """
    unique_codes = list(dict.fromkeys(codes))  # 去重且保持原有顺序
    if not unique_codes:
        return "", ""

    hints = [ACTION_HINTS[c] for c in unique_codes if c in ACTION_HINTS]
    watch_note = "；".join(hints[:2])  # 最多展示两条，保持简洁

    n = len(unique_codes)
    confidence = _ALERT_CONFIDENCE_LABELS.get(n, _ALERT_CONFIDENCE_LABEL_MAX)
    return watch_note, confidence


def is_alert_score(score: int, threshold: int) -> bool:
    """判断情绪分是否达到"重点信号"门槛（绝对值 >= threshold）。"""
    return abs(score) >= threshold


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
