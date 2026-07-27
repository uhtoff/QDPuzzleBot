from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class PuzzleImageData:
    id: int = 0
    puzzle_id: int = 0
    media_id: int = 0
    # Set when this image is a locates region's crop; None for a direct upload.
    source_region_id: Optional[int] = None
    position: int = 0
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        p = cls()
        for attr in p.__dict__:
            if attr in row:
                setattr(p, attr, row[attr])
        if p.created_at is not None:
            p.created_at = p.created_at.replace(tzinfo=datetime.timezone.utc)
        return p
