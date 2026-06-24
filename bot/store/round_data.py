from dataclasses import dataclass, fields
from dataclasses_json import dataclass_json
import datetime
import logging
from typing import List, Optional, TypeVar, Type

logger = logging.getLogger(__name__)

class MissingRoundError(RuntimeError):
    pass

@dataclass_json
@dataclass
class RoundData:
    name: str = ""
    id: int = 0
    hunt_id: int = 0
    category_id: int = 0  # round = category channel (nullable in DB for channel-less groups; M7)
    meta_id: int = 0
    meta_code: str = "0"  # 6-char handle (the DB column is CHAR(6)); was mis-typed int
    type: str = ""
    # M6 typed-group columns (additive; backfilled + kept-updated, read by nothing yet).
    kind: str = ""               # meta / metameta / metaless
    status: str = "active"       # active / solved / archived (a round is solved when its meta is)
    parent_group_id: Optional[int] = None  # group-in-group NESTING (dormant; cycle-guarded setter)
    start_time: Optional[datetime.datetime] = None
    solve_time: Optional[datetime.datetime] = None
    archive_time: Optional[datetime.datetime] = None

    T = TypeVar("T", bound="RoundData")

    @classmethod
    def import_dict(cls: Type[T], round_data: dict):
        values = {}
        for field in fields(cls):
            attr = field.name
            if attr.endswith("_time"):
                db_date = round_data.get(attr,None)
                if db_date is not None:
                    values[attr] = db_date.replace(tzinfo=datetime.timezone.utc)
                else:
                    values[attr] = None
            else:
                values[attr] = round_data.get(attr, None)
        # The double asterisk passes the dict as a series of keyword arguments
        return cls(**values)

    @classmethod
    def sort_by_round_start(cls, rounds: list) -> list:
        """Return list of PuzzleData objects sorted by round start time

        Sorts rounds by start_time.
        """
        return sorted(rounds, key=lambda p: p.start_time)

class _RoundJsonDb:
    def commit(self, round_data):
        pass
    def delete(self, round_data):
        pass
    def get_all(self, guild_id, hunt_id="*") -> List[RoundData]:
        pass
    def get_solved_rounds_to_archive(self, guild_id, now=None, include_meta=False, minutes=5) -> List[RoundData]:
        pass
    def aggregate_json(self) -> dict:
        pass
