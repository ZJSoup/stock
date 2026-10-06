from momo_bot.app import classify_tick_side, to_bar

def test_classify():
    assert classify_tick_side(6.01, 6.0, 6.01) == "B"
    assert classify_tick_side(5.99, 5.99, 6.01) == "S"

def test_to_bar():
    import datetime as dt
    class Fake:
        date = dt.datetime(2026, 9, 29, 9, 0)
        open, high, low, close, volume = 6.0, 6.1, 5.95, 6.05, 1000
    b = to_bar(Fake())
    assert b.close == 6.05 and b.volume == 1000
