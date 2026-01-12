from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
import datetime
import logging
import json
from typing import List, Optional

logger = logging.getLogger(__name__)


class MissingPuzzleError(RuntimeError):
    pass

@dataclass
class AdditionalSheetData:
    id: int = 0
    google_page_id: str = ""
    puzzle_id: int = 0
    puzzle: bool = False
    puzzle_name: str = ""
    solution: str = ""
    solved: bool = False

@dataclass_json
@dataclass
class PuzzleData:
    name: str = ""
    id: int = 0
    hunt_id: int = 0
    channel_id: int = 0
    channel_mention: str = ""
    channel_name: str = ""
    voice_channel_id: int = 0
    url: str = ""
    google_page_id: str = ""
    metapuzzle: int = 0
    additional_sheets: List[AdditionalSheetData] = field(default_factory=list)
    status: str = ""
    solved: bool = False
    archived: bool = False
    solution: str = None
    priority: str = ""
    puzzle_type: str = ""
    notes: List[str] = field(default_factory=list)
    start_time: Optional[datetime.datetime] = None
    solve_time: Optional[datetime.datetime] = None
    archive_time: Optional[datetime.datetime] = None
    tags: List[int] = field(default_factory=list)

    @classmethod
    def import_dict(cls, puzzle_data: dict):
        puz = PuzzleData()
        for attr, value in puz.__dict__.items():
            if attr == "notes":
                setattr(puz, attr, json.loads(puzzle_data.get(attr, None)))
            elif attr.endswith("_time"):
                db_date = puzzle_data.get(attr,None)
                if db_date is not None:
                    utc_date = db_date.replace(tzinfo=datetime.timezone.utc)
                    setattr(puz,attr,utc_date)
            elif attr in puzzle_data:
                setattr(puz, attr, puzzle_data.get(attr))

        return puz

    @classmethod
    def sort_by_round_start(cls, puzzles: list) -> list:
        """Return list of PuzzleData objects sorted by start of round time

        Groups puzzles in the same round together, and sorts puzzles within round
        by start_time.
        """
        round_start_times = {}

        for puzzle in puzzles:
            if puzzle.start_time is None:
                continue

            start_time = puzzle.start_time.timestamp()  # epoch time
            round_start_time = round_start_times.get(puzzle.round_name)
            if round_start_time is None or start_time < round_start_time:
                round_start_times[puzzle.round_name] = start_time

        return sorted(puzzles, key=lambda p: (round_start_times.get(p.round_name, 0), p.start_time or 0))

    @classmethod
    def sort_by_puzzle_start(cls, puzzles: list) -> list:
        """Return list of PuzzleData objects sorted by puzzle start time

        Sorts puzzles by start_time.
        """
        return sorted(puzzles, key=lambda p: p.start_time)

    @classmethod
    def sort_by_round_id(cls, puzzles: list) -> list:
        """Return list of PuzzleData objects sorted by puzzle start time

        Sorts puzzles by start_time.
        """
        return sorted(puzzles, key=lambda p: p.round_id)

    def is_metapuzzle(self) -> bool:
        return self.metapuzzle == 1

    def add_tag(self, tag):
        self.tags.append(tag)
        return

class _PuzzleJsonDb:
    def commit(self, puzzle_data):
        pass
    def delete(self, puzzle_data):
        pass
    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        pass
    def get_all(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        pass
    def get_solved_puzzles_to_archive(self, guild_id, now=None, include_meta=False, minutes=5) -> List[PuzzleData]:
        pass
    def aggregate_json(self) -> dict:
        pass
