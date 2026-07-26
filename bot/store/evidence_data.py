from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class EvidenceData:
    id: int = 0
    hunt_id: int = 0
    # None when the evidence belongs to the hunt rather than to a source puzzle
    # (e.g. an observation nobody has pinned to a specific puzzle yet).
    source_puzzle_id: Optional[int] = None
    # None unless the evidence came from a hunt-level working sheet. At most one
    # of source_puzzle_id / source_sheet_id / source_region_id is set; the service
    # methods that write each kind own that invariant.
    source_sheet_id: Optional[int] = None
    # None unless the evidence came from a region drawn on a canvas. Unlike the
    # other two sources this row is created BY the link, so canvas.py deletes it
    # when the link changes and the FK cascades it when the region goes.
    source_region_id: Optional[int] = None
    target_puzzle_id: Optional[int] = None
    note: str = ""
    media_id: Optional[int] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        e = cls()
        for attr in e.__dict__:
            if attr in row:
                setattr(e, attr, row[attr])
        # created_at is stored naive UTC (like every other timestamp column); reattach
        # tzinfo so downstream serialization/comparisons match the rest of the codebase's
        # aware datetimes (see PuzzleData.import_dict for the same pattern).
        if e.created_at is not None:
            e.created_at = e.created_at.replace(tzinfo=datetime.timezone.utc)
        return e
