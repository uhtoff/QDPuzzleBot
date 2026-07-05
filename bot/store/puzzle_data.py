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
    on_solved_sheet: bool = False
    solution: Optional[str] = None
    priority: str = ""
    puzzle_type: str = ""
    notes: List[str] = field(default_factory=list)
    start_time: Optional[datetime.datetime] = None
    solve_time: Optional[datetime.datetime] = None
    tags: List[int] = field(default_factory=list)
    tag_edges: Dict[int, set] = field(default_factory=dict)  # tag_group_id -> set of edge_type
    is_target: bool = False  # transient: derived at load from tag_group.target_puzzle_id (not a column)
    is_minimeta: bool = False  # transient: derived at load — True iff this puzzle has mini-children (not a column)
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

        Sorts puzzles by start_time. A NULL start_time (legacy/manual data)
        sorts first rather than raising, so one bad row doesn't crash the
        whole listing.
        """
        return sorted(
            puzzles,
            key=lambda p: p.start_time or datetime.datetime.min.replace(tzinfo=datetime.timezone.utc),
        )

    def add_tag(self, tag):
        if tag not in self.tags:
            self.tags.append(tag)

    def edges_for(self, tag_group_id) -> set:
        """The set of edge types this puzzle has to a group ({} if not a member)."""
        return self.tag_edges.get(tag_group_id, set())

    def has_edge(self, tag_group_id, edge) -> bool:
        return edge in self.tag_edges.get(tag_group_id, set())

    def add_edge(self, tag_group_id, edge):
        """Add one edge to a group (in-memory), keeping tags + tag_edges in sync."""
        self.tag_edges.setdefault(tag_group_id, set()).add(edge)
        self.add_tag(tag_group_id)

    def remove_edge(self, tag_group_id, edge):
        """Remove one edge; drop the whole membership if no edges remain."""
        edges = self.tag_edges.get(tag_group_id)
        if not edges:
            return
        edges.discard(edge)
        if not edges:
            self.tag_edges.pop(tag_group_id, None)
            if tag_group_id in self.tags:
                self.tags.remove(tag_group_id)

class _PuzzleJsonDb:
    def commit(self, puzzle_data):
        pass
    def delete(self, puzzle_data):
        pass
    def get(self, guild_id, puzzle_id, round_id, hunt_id) -> PuzzleData:
        raise NotImplementedError
    def get_all(self, guild_id, hunt_id="*") -> List[PuzzleData]:
        raise NotImplementedError
    def aggregate_json(self) -> dict:
        raise NotImplementedError
