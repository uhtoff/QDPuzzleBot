import datetime

from bot.core.activity_sparkline import squash

D = datetime.datetime
M = datetime.timedelta(minutes=1)
START = D(2026, 11, 1, 12, 0)


def test_three_minute_old_puzzle_gets_one_bar():
    out = squash([(START, 2, False)], START, START + 3 * M)
    assert out["bars"] == [{"chat": 2, "edited": False}]
    assert out["bar_minutes"] == 3
    assert out["last_sheet_edit_at"] is None


def test_twenty_minute_puzzle_gets_one_bar_per_slot():
    slots = [(START, 1, False), (START + 15 * M, 4, False)]
    out = squash(slots, START, START + 20 * M)
    assert [b["chat"] for b in out["bars"]] == [1, 0, 0, 4]


def test_thirty_hour_puzzle_gets_36_bars_and_conserves_counts():
    now = START + 30 * 60 * M
    slots = [(START, 3, False), (START + 600 * M, 5, False),
             (START + 1200 * M, 7, True), (now - 5 * M, 11, False)]
    out = squash(slots, START, now)
    bars = out["bars"]
    assert len(bars) == 36
    assert sum(b["chat"] for b in bars) == 26
    assert bars[0]["chat"] == 3
    assert bars[35]["chat"] == 11
    assert out["bar_minutes"] == 50


def test_shape_given_up():
    slots = [(START + i * 5 * M, 4, False) for i in range(12)]  # first hour only
    out = squash(slots, START, START + 6 * 60 * M)
    chats = [b["chat"] for b in out["bars"]]
    assert len(chats) == 36
    assert all(c > 0 for c in chats[:6])
    assert all(c == 0 for c in chats[6:])


def test_edited_flag_and_last_edit():
    early, late = START + 10 * M, START + 100 * M
    slots = [(early, 0, True), (START + 50 * M, 2, False), (late, 1, True)]
    out = squash(slots, START, START + 180 * M)
    edited = [i for i, b in enumerate(out["bars"]) if b["edited"]]
    assert edited == [2, 20]
    assert out["last_sheet_edit_at"] == late


def test_slot_before_start_clamps_to_first_bar():
    out = squash([(START, 2, False)], START + 2 * M, START + 60 * M)
    assert out["bars"][0]["chat"] == 2
    assert sum(b["chat"] for b in out["bars"]) == 2
