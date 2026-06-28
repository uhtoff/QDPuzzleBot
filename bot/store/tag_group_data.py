from dataclasses import dataclass, fields
from dataclasses_json import dataclass_json
import datetime
import logging
from typing import List, Optional, TypeVar, Type

logger = logging.getLogger(__name__)

# Edge-type vocabulary constants — the allowed values.
EDGE_FEEDS = "feeds"
EDGE_INFORMS = "informs"
EDGE_NONE = "none"
# A target's self-edge: it is a member of its own group but neither feeds nor
# informs it. Distinct from 'none' (a pooled member) so the target is
# distinguishable from genuine pooled feeders without an id comparison.
EDGE_TARGET = "target"

class MissingTagGroupError(RuntimeError):
    pass

@dataclass_json
@dataclass
class TagGroupData:
    name: str = ""
    id: int = 0
    hunt_id: int = 0
    # The group's "home container" id in Discord — but WHICH kind of object it
    # points at depends on how the group was created:
    #   * normal round / meta (channel layout) -> a Discord CATEGORY id
    #   * forum-layout puzzle                  -> a FORUM CHANNEL id
    #   * metaless Group (channel layout)      -> its cloned Discord CATEGORY id
    #   * metaless Group (forum layout)        -> its text channel id
    #   * in-place-converted puzzle            -> the puzzle's own TEXT CHANNEL id
    # So this is a "group home", not always a category. To resolve it as a Discord
    # category (for channel placement/moves), use
    # category_layout.primary_category(guild, tag_group) — it returns None for the
    # non-category kinds. (Nullable in DB for channel-less groups; M7.)
    category_id: int = 0
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

@dataclass
class GroupMatch:
    """Outcome of resolving a user-supplied group reference.

    status:
      "exact"     — matched a group_code; ``group`` is set.
      "name"      — matched a group name (exact or unique substring); ``group`` is set.
      "ambiguous" — several groups matched; ``candidates`` lists them, ``group`` is None.
      "none"      — nothing matched; ``group`` is None, ``candidates`` empty.
      "empty"     — no input given; ``candidates`` holds the available metas as suggestions.
    """
    group: Optional["TagGroupData"]
    candidates: List["TagGroupData"]
    status: str


def resolve_group(raw, groups) -> GroupMatch:
    """Resolve a user-supplied group reference to a single TagGroupData.

    ``groups`` is the candidate pool (caller scopes it, e.g. to one hunt). Only metas
    (a 6-char string ``group_code``) are eligible. Resolution order, all case-insensitive
    and whitespace-trimmed:

      1. exact ``group_code``
      2. exact ``name``
      3. unique substring over ``name``/``group_code``/``id``

    Multiple matches at any step short-circuit to an ``ambiguous`` result so the caller can
    ask the user to disambiguate rather than silently picking one.
    """
    metas = [g for g in groups if isinstance(g.group_code, str) and len(g.group_code) == 6]
    norm = (raw or "").strip()
    if not norm:
        return GroupMatch(None, metas, "empty")

    code = norm.upper()
    by_code = [g for g in metas if g.group_code.upper() == code]
    if len(by_code) == 1:
        return GroupMatch(by_code[0], [], "exact")
    if len(by_code) > 1:
        return GroupMatch(None, by_code, "ambiguous")

    low = norm.lower()
    by_name = [g for g in metas if g.name.lower() == low]
    if len(by_name) == 1:
        return GroupMatch(by_name[0], [], "name")
    if len(by_name) > 1:
        return GroupMatch(None, by_name, "ambiguous")

    by_sub = [g for g in metas if low in f"{g.name} {g.group_code} {g.id}".lower()]
    if len(by_sub) == 1:
        return GroupMatch(by_sub[0], [], "name")
    if len(by_sub) > 1:
        return GroupMatch(None, by_sub, "ambiguous")

    return GroupMatch(None, [], "none")


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
