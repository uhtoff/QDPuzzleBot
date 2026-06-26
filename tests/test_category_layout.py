from unittest.mock import MagicMock

from bot.store.tag_group_category_data import (
    TagGroupCategoryData, ROLE_UNSOLVED, ROLE_SOLVED,
)


def test_tag_group_category_defaults():
    row = TagGroupCategoryData(group_id=5, category_id=999)
    assert row.role == ROLE_UNSOLVED
    assert row.seq == 0
    assert row.id == 0


def test_role_constants():
    assert ROLE_UNSOLVED == "unsolved"
    assert ROLE_SOLVED == "solved"


from bot.core import category_layout as layout


def test_solved_category_name():
    assert layout.solved_category_name("round-1") == "round-1 — solved"


def test_overflow_category_name_unsolved():
    assert layout.overflow_category_name("round-1", "unsolved", 0) == "round-1"
    assert layout.overflow_category_name("round-1", "unsolved", 1) == "round-1 (2)"
    assert layout.overflow_category_name("round-1", "unsolved", 2) == "round-1 (3)"


def test_overflow_category_name_solved():
    assert layout.overflow_category_name("round-1", "solved", 0) == "round-1 — solved"
    assert layout.overflow_category_name("round-1", "solved", 1) == "round-1 — solved (2)"


def test_puzzle_count_excludes_divider():
    from bot.core.discord_channels import SOLVE_DIVIDER
    div = MagicMock(); div.name = SOLVE_DIVIDER
    a = MagicMock(); a.name = "puz-a"
    b = MagicMock(); b.name = "puz-b"
    cat = MagicMock(); cat.channels = [div, a, b]
    assert layout.puzzle_count(cat) == 2


def test_is_split_false_when_no_rows():
    dao = MagicMock()
    dao.get_for_group.return_value = []
    tag_group = MagicMock(id=5)
    assert layout.is_split(tag_group, dao) is False


def test_is_split_true_when_rows_present():
    dao = MagicMock()
    dao.get_for_group.return_value = [TagGroupCategoryData(group_id=5, category_id=9)]
    tag_group = MagicMock(id=5)
    assert layout.is_split(tag_group, dao) is True


def test_soft_limit_below_hard_cap():
    from bot.core.discord_channels import MAX_CHANNELS_PER_CATEGORY
    assert layout.CATEGORY_SOFT_LIMIT < MAX_CHANNELS_PER_CATEGORY
