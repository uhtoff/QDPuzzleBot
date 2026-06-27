from dataclasses import dataclass
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
    archive_google_sheet_id: str = ""
    url: str = ""
    url_sep: str = "-"
    puzzle_prefix: str = "puzzle"
    archived: bool = False
    layout_mode: str = "channel"
    role_id: int = 0
    start_timestamp: int = 0
    username: str = ""
    password: str = ""
    start_time: Optional[datetime.datetime] = None
    solve_time: Optional[datetime.datetime] = None
    archive_time: Optional[datetime.datetime] = None
    evidence_sheet_page_id: Optional[int] = None
    unassigned_sheet_page_id: Optional[int] = None
    unassigned_table_id: Optional[int] = None

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
