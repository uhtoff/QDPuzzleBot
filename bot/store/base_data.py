from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
import datetime
import logging
import json
from typing import List, Optional

logger = logging.getLogger(__name__)

@dataclass_json
@dataclass
class _BaseData:
    def add_data_from_dict(self, data: dict):
        for attr, value in self.__dict__.items():
            if attr == "notes":
                setattr(puz, attr, json.loads(puzzle_data.get(attr, None)))
            elif attr.endswith("_time"):
                db_date = puzzle_data.get(attr,None)
                if db_date is not None:
                    utc_date = db_date.replace(tzinfo=datetime.timezone.utc)
                    setattr(puz,attr,utc_date)
            else:
                setattr(puz, attr, puzzle_data.get(attr, None))