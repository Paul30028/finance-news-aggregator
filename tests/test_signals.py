from app.processing.signals import compute_sentiment_score, decode_signal_tags, encode_signal_tags, extract_signals


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
