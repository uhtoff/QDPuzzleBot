"""Squash a puzzle's 5-minute activity slots into sparkline bars.

Pure: the dashboard payload (bot/web/data.py:get_hunt_puzzles) calls it with the
slot rows and the puzzle's life span. Every puzzle gets bars spanning its whole
life, unlock -> now, so each has its own time scale; ``bar_minutes`` says how
long one bar is, so the frontend can draw chat as a rate on a shared scale.
"""
import datetime
import math

from bot.core.activity_slots import SLOT_MINUTES

MAX_BARS = 36

Slot = tuple[datetime.datetime, int, bool]  # (slot_start, chat_count, sheet_edited)


def squash(slots: list[Slot], start: datetime.datetime, now: datetime.datetime,
           max_bars: int = MAX_BARS) -> dict:
    """``{"bar_minutes", "bars": [{"chat", "edited"}], "last_sheet_edit_at"}``.

    Bar count is one per 5-minute slot of the span, at least 1 and at most
    ``max_bars``, so no bar is narrower than a slot and every slot lands in
    exactly one bar (by its start; slots outside [start, now) clamp to the ends).
    """
    span = max((now - start).total_seconds(), 0.0)
    n = max(1, min(max_bars, math.ceil(span / (SLOT_MINUTES * 60))))
    bars = [{"chat": 0, "edited": False} for _ in range(n)]
    last_edit = None
    for at, chat, edited in slots:
        offset = (at - start).total_seconds()
        i = int(offset / span * n) if span else 0
        bar = bars[min(max(i, 0), n - 1)]
        bar["chat"] += chat
        if edited:
            bar["edited"] = True
            if last_edit is None or at > last_edit:
                last_edit = at
    return {"bar_minutes": span / 60 / n, "bars": bars, "last_sheet_edit_at": last_edit}
