from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
import datetime
import logging
import json
from typing import List, Optional

logger = logging.getLogger(__name__)


class MissingHuntError(RuntimeError):
    pass

@dataclass_json
@dataclass
class HuntData:
    name: str = ""
    id: int = 0
    uid: str = ""
    category_id: int = 0
    channel_id: int = 0
    guild_id: int = 0
    google_sheet_id: str = ""
    url: str = ""
    url_sep: str = "-"
    puzzle_prefix: str = "puzzle"
    parallel_hunt: bool = False
    role_id: int = 0
    num_rounds: int = 0
    username: str = ""
    password: str = ""
    start_time: Optional[datetime.datetime] = None
    solve_time: Optional[datetime.datetime] = None
    archive_time: Optional[datetime.datetime] = None

    def __hash__(self):
        return self.id
    @classmethod
    def import_dict(cls, hunt_data: dict):
        r = HuntData()
        for attr, value in r.__dict__.items():
            if attr == "notes":
                setattr(r, attr, json.loads(hunt_data.get(attr, None)))
            elif attr.endswith("_time"):
                db_date = hunt_data.get(attr,None)
                if db_date is not None:
                    utc_date = db_date.replace(tzinfo=datetime.timezone.utc)
                    setattr(r,attr,utc_date)
            else:
                setattr(r, attr, hunt_data.get(attr, None))

        return r

    @classmethod
    def sort_by_round_start(cls, rounds: list) -> list:
        """Return list of PuzzleData objects sorted by start of round time

        Groups rounds in the same round together, and sorts rounds within round
        by start_time.
        """
        round_start_times = {}

        for round in rounds:
            if round.start_time is None:
                continue

            start_time = round.start_time.timestamp()  # epoch time
            round_start_time = round_start_times.get(round.round_name)
            if round_start_time is None or start_time < round_start_time:
                round_start_times[round.round_name] = start_time

        return sorted(rounds, key=lambda p: (round_start_times.get(p.round_name, 0), p.start_time or 0))

    @classmethod
    def sort_by_round_start(cls, rounds: list) -> list:
        """Return list of PuzzleData objects sorted by round start time

        Sorts rounds by start_time.
        """
        return sorted(rounds, key=lambda p: p.start_time)

class _HuntJsonDb:
    def commit(self, round_data):
        pass
    def delete(self, round_data):
        pass
    def get(self, guild_id, round_id, hunt_id) -> HuntData:
        pass
    def get_all(self, guild_id, hunt_id="*") -> List[HuntData]:
        pass
    def get_solved_rounds_to_archive(self, guild_id, now=None, include_meta=False, minutes=5) -> List[HuntData]:
        pass
    def aggregate_json(self) -> dict:
        pass
