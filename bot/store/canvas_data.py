from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class HuntCanvasData:
    id: int = 0
    hunt_id: int = 0
    media_id: int = 0
    name: str = ""
    note: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        c = cls()
        for attr in c.__dict__:
            if attr in row:
                setattr(c, attr, row[attr])
        # Naive UTC in the column, aware everywhere else (see MediaData).
        if c.created_at is not None:
            c.created_at = c.created_at.replace(tzinfo=datetime.timezone.utc)
        return c


@dataclass
class CanvasRegionData:
    id: int = 0
    canvas_id: int = 0
    # JSON: {"type":"rect","points":[[x1,y1],[x2,y2]]}, normalised 0-1. Stored as
    # written by canvas_shape.parse_shape, which is the only thing that builds it.
    shape: str = ""
    label: str = ""
    note: Optional[str] = None
    puzzle_id: Optional[int] = None
    # 'locates' or 'informs'; None exactly when puzzle_id is None.
    role: Optional[str] = None
    # The region's extracted crop, written by the web layer.
    crop_media_id: Optional[int] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        r = cls()
        for attr in r.__dict__:
            if attr in row:
                setattr(r, attr, row[attr])
        if r.created_at is not None:
            r.created_at = r.created_at.replace(tzinfo=datetime.timezone.utc)
        return r
