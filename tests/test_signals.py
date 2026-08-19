from app.processing.signals import (
    ACTION_HINTS,
    SIGNAL_RULES,
    compute_sentiment_score,
    conclusion_for_codes,
    decode_signal_tags,
    encode_signal_tags,
    extract_signals,
    is_alert_score,
)


def test_extract_signals_detects_rate_cut_as_bullish():
    hits = extract_signals("央行宣布降息25个基点", "市场反应积极")
    codes = {h.code for h in hits}
    assert "rate_cut" in codes
    assert all(h.polarity == 1 for h in hits if h.code == "rate_cut")


def test_extract_signals_detects_earnings_miss_as_bearish():
    hits = extract_signals("公司发布业绩预警", "净利润下降超过三成")
    codes = {h.code for h in hits}
    assert "earnings_miss" in codes
    assert compute_sentiment_score(hits) < 0


def test_extract_signals_is_case_insensitive_for_english_keywords():
    hits = extract_signals("Fed Cuts Rates by 25bps", "")
    assert any(h.code == "rate_cut" for h in hits)


def test_extract_signals_dedupes_per_signal_type():
    # 标题和摘要都出现"降息"，但同一信号类型只应计入一次
    hits = extract_signals("央行降息", "本次降息力度超预期")
    assert len([h for h in hits if h.code == "rate_cut"]) == 1


def test_extract_signals_returns_empty_for_neutral_news():
    hits = extract_signals("公司召开年度股东大会", "会议按计划举行")
    assert hits == []
    assert compute_sentiment_score(hits) == 0


def test_compute_sentiment_score_sums_polarities_of_mixed_signals():
    # 降息(+1) + 业绩不及预期(-1) => 净分应为 0
    hits = extract_signals("央行降息但公司业绩不及预期", "")
    assert compute_sentiment_score(hits) == 0


def test_encode_decode_signal_tags_roundtrip():
    hits = extract_signals("公司宣布股票回购计划", "")
    encoded = encode_signal_tags(hits)
    assert encoded == "buyback_dividend"
    decoded = decode_signal_tags(encoded)
    assert decoded == [("buyback_dividend", "回购/增持/分红", 1)]


def test_decode_signal_tags_handles_none_and_empty():
    assert decode_signal_tags(None) == []
    assert decode_signal_tags("") == []


def test_every_signal_rule_has_an_action_hint():
    # 每个信号类型都应该有对应的"关注建议"，否则 conclusion_for_codes 会静默漏掉它
    for code, _label, _polarity, _keywords in SIGNAL_RULES:
        assert code in ACTION_HINTS, f"信号 '{code}' 缺少 ACTION_HINTS 条目"


def test_conclusion_for_codes_empty_when_no_hits():
    assert conclusion_for_codes([]) == ("", "")


def test_conclusion_for_codes_single_signal():
    watch_note, confidence = conclusion_for_codes(["rate_cut"])
    assert watch_note == ACTION_HINTS["rate_cut"]
    assert "单一信号" in confidence


def test_conclusion_for_codes_multiple_signals_raises_confidence():
    watch_note, confidence = conclusion_for_codes(["rate_cut", "buyback_dividend"])
    assert ACTION_HINTS["rate_cut"] in watch_note
    assert ACTION_HINTS["buyback_dividend"] in watch_note
    assert "双重信号" in confidence


def test_conclusion_for_codes_dedupes_repeated_codes():
    # 同一信号代码出现两次不应被当成"双重信号"
    _, confidence = conclusion_for_codes(["rate_cut", "rate_cut"])
    assert "单一信号" in confidence


def test_conclusion_for_codes_three_or_more_uses_max_label():
    _, confidence = conclusion_for_codes(["rate_cut", "buyback_dividend", "upgrade"])
    assert confidence == "多重信号叠加，建议优先关注"


def test_is_alert_score_respects_threshold():
    assert is_alert_score(2, threshold=2) is True
    assert is_alert_score(-2, threshold=2) is True
    assert is_alert_score(1, threshold=2) is False
    assert is_alert_score(0, threshold=2) is False
