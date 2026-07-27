from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class MediaMarkupData:
    id: int = 0
    media_id: int = 0
    # 'pen' | 'line' | 'text'
    kind: str = ""
    # JSON as written by markup_shape.parse_mark — the only thing that builds it.
    shape: str = ""
    # JSON as written by markup_shape.parse_style.
    style: str = ""
    text: Optional[str] = None
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        m = cls()
        for attr in m.__dict__:
            if attr in row:
                setattr(m, attr, row[attr])
        # Naive UTC in the column, aware everywhere else (see MediaData).
        if m.created_at is not None:
            m.created_at = m.created_at.replace(tzinfo=datetime.timezone.utc)
        return m
