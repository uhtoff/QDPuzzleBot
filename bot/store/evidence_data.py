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
