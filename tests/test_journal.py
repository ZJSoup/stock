from momo_bot.journal import record_trade, record_violation, summarize

def trade(date, symbol, pnl):
    return {"date": date, "symbol": symbol, "grade": "A+", "shares": 100,
            "entry": 6.0, "exit": 6.5, "pnl": pnl, "reason": "signal"}

def test_journal_and_gate(tmp_path):
    # 10 winning setups over 20 distinct trading days (one setup every other day)
    for i in range(10):
        record_trade(tmp_path, trade(f"2026-09-{i+1:02d}", "ZTG", 50))
    # pad days without trades by violation-free empty days is not needed: trading_days = distinct trade dates here
    stats = summarize(tmp_path)
    assert stats["setups"] == 10
    assert stats["trading_days"] == 10
    assert stats["net"] == 500
    assert stats["win_rate"] == 1.0
    assert stats["violations"] == 0
    assert stats["eligible"] is False  # trading_days < 20

def test_drawdown_and_violations_block_gate(tmp_path):
    days = [f"2026-09-{i+1:02d}" for i in range(20)]
    pnls = [100]*5 + [-300]*3 + [100]*12
    for d, p in zip(days, pnls):
        record_trade(tmp_path, trade(d, "X", p))
    record_violation(tmp_path, "naked position", "2026-09-10")
    stats = summarize(tmp_path)
    assert stats["violations"] == 1
    assert stats["max_drawdown"] > 0.15
    assert stats["eligible"] is False
