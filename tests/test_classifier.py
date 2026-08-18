from app.processing.classifier import classify

CATEGORIES = {
    "宏观": ["央行", "降息", "CPI"],
    "A股": ["沪指", "涨停"],
    "港美股": ["纳斯达克", "美股"],
}


def test_classify_picks_category_with_most_hits():
    result = classify("央行宣布降息，CPI数据低于预期", "", CATEGORIES)
    assert result == "宏观"


def test_classify_defaults_to_other_when_no_hits():
    result = classify("今天天气不错", "", CATEGORIES)
    assert result == "其他"


def test_classify_uses_category_hint_as_tiebreaker():
    # 标题中"美股"命中港美股一次，"沪指"命中A股一次，打平时 hint 应决定结果
    result = classify("美股与沪指今日走势", "", CATEGORIES, category_hint="港美股")
    assert result == "港美股"
