from momo_bot.scanner import to_candidate

def test_to_candidate_grades():
    c = to_candidate("ZTG", 123, last=5.78, prev_close=4.0, volume=5_000_000,
                     avg_volume=100_000, float_shares=8_000_000, has_news=False)
    assert c is not None and c.grade == "B"
    assert round(c.rvol, 1) == 50.0

def test_to_candidate_rejects():
    assert to_candidate("X", 1, 1.0, 1.0, 0, 1, 5_000_000, True) is None