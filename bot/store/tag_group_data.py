from dataclasses import dataclass, fields
from dataclasses_json import dataclass_json
import datetime
import logging
from typing import List, Optional, TypeVar, Type

logger = logging.getLogger(__name__)

# Edge-type vocabulary constants — the three allowed values.
EDGE_FEEDS = "feeds"
EDGE_INFORMS = "informs"
EDGE_NONE = "none"

class MissingTagGroupError(RuntimeError):
    pass

@dataclass_json
@dataclass
class TagGroupData:
    name: str = ""
    id: int = 0
    hunt_id: int = 0
    category_id: int = 0  # tag_group = category channel (nullable in DB for channel-less groups; M7)
    target_puzzle_id: int = 0
    group_code: str = "0"  # 6-char handle (the DB column is CHAR(6))
    type: str = ""
    status: str = "active"       # active / solved / archived (a tag_group is solved when its target is)
    parent_group_id: Optional[int] = None  # group-in-group NESTING (dormant; cycle-guarded setter)
    start_time: Optional[datetime.datetime] = None
    solve_time: Optional[datetime.datetime] = None
    archive_time: Optional[datetime.datetime] = None

    @property
    def has_target(self) -> bool:
        """True if this group has a target puzzle (not a metaless/Group type)."""
        return bool(self.target_puzzle_id)

    T = TypeVar("T", bound="TagGroupData")

    @classmethod
    def import_dict(cls: Type[T], tag_group_data: dict):
        values = {}
        for field in fields(cls):
            attr = field.name
            if attr.endswith("_time"):
                db_date = tag_group_data.get(attr,None)
                if db_date is not None:
                    values[attr] = db_date.replace(tzinfo=datetime.timezone.utc)
                else:
                    values[attr] = None
            else:
                values[attr] = tag_group_data.get(attr, None)
        # The double asterisk passes the dict as a series of keyword arguments
        return cls(**values)

    @classmethod
    def sort_by_tag_group_start(cls, tag_groups: list) -> list:
        """Return list of TagGroupData objects sorted by start time."""
        return sorted(tag_groups, key=lambda p: p.start_time)

class _TagGroupJsonDb:
    def commit(self, tag_group_data):
        pass
    def delete(self, tag_group_data):
        pass
    def get_all(self, guild_id, hunt_id="*") -> List[TagGroupData]:
        pass
    def get_solved_rounds_to_archive(self, guild_id, now=None, include_meta=False, minutes=5) -> List[TagGroupData]:
        pass
    def aggregate_json(self) -> dict:
        pass
