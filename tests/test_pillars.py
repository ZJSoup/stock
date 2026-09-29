from momo_bot.pillars import relative_volume, grade_pillars


def test_rvol():
    assert relative_volume(today_volume=5_000_000, avg_volume=1_000_000) == 5.0


def test_a_plus():
    assert grade_pillars(0.40, 8.0, 7.5, 8_000_000, True) == "A+"


def test_a_when_above_10_but_under_30():
    assert grade_pillars(0.15, 6.0, 7.5, 15_000_000, True) == "A"


def test_b_when_news_missing():
    # 4/5 pillars: no news -> squeeze stock, reduced size
    assert grade_pillars(0.40, 8.0, 7.5, 8_000_000, False) == "B"


def test_rejected_when_rvol_too_low():
    assert grade_pillars(0.40, 3.0, 7.5, 8_000_000, True) is None


def test_rejected_when_price_outside_range():
    assert grade_pillars(0.40, 8.0, 0.80, 8_000_000, True) is None
    assert grade_pillars(0.40, 8.0, 25.0, 8_000_000, True) is None


def test_rejected_when_float_too_big():
    assert grade_pillars(0.40, 8.0, 7.5, 30_000_000, True) is None
