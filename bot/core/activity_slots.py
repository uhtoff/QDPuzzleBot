"""Five-minute activity slots: the unit puzzle activity is recorded in.

Chat messages and sheet edits are counted per (puzzle, slot) in the
puzzle_activity_slots table; the dashboard squashes the slots into sparkline bars
(see bot/core/activity_sparkline.py).
"""
import datetime

SLOT_MINUTES = 5


def slot_start(at: datetime.datetime) -> datetime.datetime:
    """The start of the slot containing ``at``, as naive UTC (the schema's convention).

    A naive ``at`` is taken to be UTC already; an aware one is converted.
    """
    if at.tzinfo is not None:
        at = at.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return at.replace(minute=at.minute - at.minute % SLOT_MINUTES, second=0, microsecond=0)
