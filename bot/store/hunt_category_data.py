from dataclasses import dataclass
from dataclasses_json import dataclass_json

from bot.store.tag_group_category_data import ROLE_UNSOLVED


@dataclass_json
@dataclass
class HuntCategoryData:
    """One Discord category owned by a hunt's general container, with its role
    and ordering — the hunt-level counterpart to TagGroupCategoryData.

    A hunt with no rows is in legacy 'single category + divider' layout.
    """
    id: int = 0
    hunt_id: int = 0
    category_id: int = 0
    role: str = ROLE_UNSOLVED   # 'unsolved' | 'solved'
    seq: int = 0
