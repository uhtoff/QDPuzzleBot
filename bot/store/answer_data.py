import datetime
from dataclasses import dataclass
from typing import Optional


@dataclass
class AnswerData:
    """One row of puzzle_answers (migration 064). created_at is UTC-aware on read,
    like the *_time fields in PuzzleData.import_dict."""
    id: int = 0
    answer: str = ""
    position: int = 0
    no_meta: bool = False
    created_by: Optional[str] = None
    created_at: Optional[datetime.datetime] = None
