from momo_bot.state import Snapshot, save_snapshot, load_snapshot, clear_snapshot


def snap():
    return Snapshot(
        date="2026-09-29", phase="IN_POSITION", symbol="ZTG", con_id=123,
        grade="B", shares=300, position_qty=300, entry=6.0, stop=5.9, target=6.2,
        realized_pnl=0.0, peak_pnl=12.0, reduced=False, done=False,
    )


def test_roundtrip(tmp_path):
    save_snapshot(tmp_path, snap())
    loaded = load_snapshot(tmp_path, "2026-09-29")
    assert loaded == snap()


def test_missing_returns_none(tmp_path):
    assert load_snapshot(tmp_path, "2026-09-29") is None


def test_done_lock_survives(tmp_path):
    save_snapshot(tmp_path, Snapshot("2026-09-29", "DONE", None, None, 0, 0,
                                     None, None, None, -50.0, 0.0, False, True))
    assert load_snapshot(tmp_path, "2026-09-29").done is True
    clear_snapshot(tmp_path, "2026-09-29")
    assert load_snapshot(tmp_path, "2026-09-29") is None


def test_no_tmp_files_left(tmp_path):
    save_snapshot(tmp_path, snap())
    assert [p.name for p in tmp_path.iterdir()] == ["state-2026-09-29.json"]
