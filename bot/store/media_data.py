from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class MediaData:
    id: int = 0
    hunt_id: int = 0
    path: str = ""
    filename: str = ""
    content_type: str = ""
    size: int = 0
    origin: str = "upload"
    width: int = 0
    height: int = 0
    # none: no pyramid wanted. pending: queued or running. ready: on disk.
    # failed: tiling gave up; callers fall back to the whole image.
    tile_status: str = "none"
    tile_levels: int = 0
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        m = cls()
        for attr in m.__dict__:
            if attr in row:
                setattr(m, attr, row[attr])
        # created_at is stored naive UTC (like every other timestamp column);
        # reattach tzinfo so downstream serialization/comparisons match the rest
        # of the codebase's aware datetimes (see EvidenceData.import_dict).
        if m.created_at is not None:
            m.created_at = m.created_at.replace(tzinfo=datetime.timezone.utc)
        return m
