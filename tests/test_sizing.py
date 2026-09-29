from momo_bot.sizing import risk_shares, cash_shares, calc_shares

def test_risk_shares():
    # risk $200, stop distance $0.25 -> 800 shares
    assert risk_shares(equity=2000, risk_pct=0.10, entry=6.0, stop=5.75) == 800

def test_cash_shares():
    assert cash_shares(settled_cash=2000, price=7.5) == 266

def test_cash_cap_wins_in_small_account():
    # risk says 800 shares ($4,800) but cash allows only ~333
    shares = calc_shares(2000, 2000, entry=6.0, stop=5.75, grade="A+", risk_pct=0.10)
    assert shares == 333

def test_grade_factor_reduces_size():
    shares_a = calc_shares(20000, 20000, entry=6.0, stop=5.75, grade="A+", risk_pct=0.10)
    shares_b = calc_shares(20000, 20000, entry=6.0, stop=5.75, grade="B", risk_pct=0.10)
    assert shares_b == int(shares_a * 0.5)
