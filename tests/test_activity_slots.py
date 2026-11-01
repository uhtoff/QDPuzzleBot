import datetime

from bot.core.activity_slots import SLOT_MINUTES, slot_start


def test_slot_start_floors_to_five_minutes():
    assert SLOT_MINUTES == 5
    d = datetime.datetime
    assert slot_start(d(2026, 11, 1, 12, 7, 59)) == d(2026, 11, 1, 12, 5)
    assert slot_start(d(2026, 11, 1, 12, 5)) == d(2026, 11, 1, 12, 5)
    assert slot_start(d(2026, 11, 1, 12, 4, 59, 900000)) == d(2026, 11, 1, 12, 0)


def test_slot_start_converts_aware_to_naive_utc():
    plus_one = datetime.timezone(datetime.timedelta(hours=1))
    got = slot_start(datetime.datetime(2026, 11, 1, 13, 7, tzinfo=plus_one))
    assert got == datetime.datetime(2026, 11, 1, 12, 5)
    assert got.tzinfo is None
