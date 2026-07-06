from bot.store.puzzle_data import PuzzleData
from bot.store.tag_group_data import (
    TagGroupData, EDGE_FEEDS, EDGE_INFORMS, EDGE_TARGET, EDGE_NONE,
)
from bot.core.target_extract import select_extract_puzzles

META_ID = 10


def _puzzle(pid, edges):
    """edges: list of (tag_group_id, edge_type)."""
    p = PuzzleData(name=f"puzzle-{pid}")
    p.id = pid
    for gid, edge in edges:
        p.add_edge(gid, edge)
    return p


def _meta(target_id=1):
    m = TagGroupData(name="the-meta")
    m.id = META_ID
    m.target_puzzle_id = target_id
    m.type = "Metapuzzle"
    return m


def test_target_puzzle_is_first():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    result = select_extract_puzzles(meta, target, [target], target_puzzle_ids={1})
    assert [p.id for p in result] == [1]


def test_exclusive_plain_feeder_is_selected():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    feeder = _puzzle(2, [(META_ID, EDGE_FEEDS)])
    result = select_extract_puzzles(meta, target, [target, feeder], target_puzzle_ids={1})
    assert [p.id for p in result] == [1, 2]


def test_informs_feeder_is_selected():
    meta = _meta()
    meta.type = "Capstone"
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    feeder = _puzzle(2, [(META_ID, EDGE_INFORMS)])
    result = select_extract_puzzles(meta, target, [target, feeder], target_puzzle_ids={1})
    assert [p.id for p in result] == [1, 2]


def test_shared_feeder_is_left_behind():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    shared = _puzzle(2, [(META_ID, EDGE_FEEDS), (99, EDGE_FEEDS)])
    result = select_extract_puzzles(meta, target, [target, shared], target_puzzle_ids={1})
    assert [p.id for p in result] == [1]


def test_sub_meta_is_left_behind():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    sub_meta = _puzzle(3, [(META_ID, EDGE_FEEDS)])  # feeds meta but is itself a target
    result = select_extract_puzzles(meta, target, [target, sub_meta], target_puzzle_ids={1, 3})
    assert [p.id for p in result] == [1]


def test_non_feeder_member_is_left_behind():
    meta = _meta()
    target = _puzzle(1, [(META_ID, EDGE_TARGET)])
    pooled = _puzzle(4, [(META_ID, EDGE_NONE)])
    result = select_extract_puzzles(meta, target, [target, pooled], target_puzzle_ids={1})
    assert [p.id for p in result] == [1]
