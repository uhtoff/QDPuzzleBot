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
