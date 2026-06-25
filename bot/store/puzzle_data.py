from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
import datetime
import logging
import json
from typing import Dict, List, Optional

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
    tag_edges: Dict[int, str] = field(default_factory=dict)  # M7: tag_group_id -> edge_type
    is_target: bool = False  # transient: derived at load from tag_group.target_puzzle_id (not a column)
    parent_puzzle_id: Optional[int] = None

    @classmethod
    def import_dict(cls, puzzle_data: dict):
        puz = PuzzleData()
        for attr, value in puz.__dict__.items():
            if attr == "notes":
                # A NULL/empty notes column would make json.loads crash the whole
                # store read; fall back to the dataclass default ([]) instead.
                raw_notes = puzzle_data.get(attr)
                if raw_notes:
                    try:
                        setattr(puz, attr, json.loads(raw_notes))
                    except (TypeError, ValueError):
                        logger.warning(
                            "Could not parse notes for puzzle %s: %r",
                            puzzle_data.get("id"), raw_notes,
                        )
            elif attr.endswith("_time"):
                db_date = puzzle_data.get(attr,None)
                if db_date is not None:
                    utc_date = db_date.replace(tzinfo=datetime.timezone.utc)
                    setattr(puz,attr,utc_date)
            elif attr in puzzle_data:
                setattr(puz, attr, puzzle_data.get(attr))

        return puz

    @classmethod
    def sort_by_puzzle_start(cls, puzzles: list) -> list:
        """Return list of PuzzleData objects sorted by puzzle start time

        Sorts puzzles by start_time.
        """
        return sorted(puzzles, key=lambda p: p.start_time)

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
