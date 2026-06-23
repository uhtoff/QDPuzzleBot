from dataclasses import dataclass
import datetime
from typing import Optional


@dataclass
class EvidenceData:
    id: int = 0
    hunt_id: int = 0
    source_puzzle_id: int = 0
    target_puzzle_id: Optional[int] = None
    note: str = ""
    created_at: Optional[datetime.datetime] = None

    @classmethod
    def import_dict(cls, row: dict):
        e = cls()
        for attr in e.__dict__:
            if attr in row:
                setattr(e, attr, row[attr])
        return e
