from dataclasses import dataclass
from dataclasses_json import dataclass_json

ROLE_UNSOLVED = "unsolved"
ROLE_SOLVED = "solved"


@dataclass_json
@dataclass
class TagGroupCategoryData:
    """One Discord category owned by a tag_group, with its role and ordering.

    A group with no rows is in legacy 'single category + divider' layout.
    """
    id: int = 0
    group_id: int = 0
    category_id: int = 0
    role: str = ROLE_UNSOLVED   # 'unsolved' | 'solved'
    seq: int = 0
