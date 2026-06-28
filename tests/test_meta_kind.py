"""Unit tests for derive_meta_kind — the single derived label shown per target
puzzle, by precedence Runaround > Metameta > Meta > Capstone.

derive_meta_kind operates on the plain puzzle dicts built by get_hunt_puzzles, so
these tests build the same minimal shape (id, is_meta, rounds[{id, is_target_of,
edge_type, group_type}]) without touching the DB.
"""
from bot.web.data import derive_meta_kind


def _target(pid, *, group_id, group_type="Metapuzzle"):
    """A target puzzle that is the meta/capstone of one group."""
    return {
        "id": pid,
        "is_meta": True,
        "rounds": [
            {"id": group_id, "is_target_of": True, "edges": [], "group_type": group_type},
        ],
    }


def _feeder(pid, *, group_id, edge_type, is_meta=False):
    """A feeder puzzle tagged into a group on the given edge."""
    return {
        "id": pid,
        "is_meta": is_meta,
        "rounds": [
            {"id": group_id, "is_target_of": False, "edges": [edge_type], "group_type": "Metapuzzle"},
        ],
    }


def _kind(puzzles, pid):
    derive_meta_kind(puzzles)
    return next(p["meta_kind"] for p in puzzles if p["id"] == pid)


def test_non_target_is_none():
    p = _feeder(2, group_id=1, edge_type="feeds")
    assert _kind([p], 2) is None


def test_feeds_feeder_makes_meta():
    meta = _target(1, group_id=1)
    feeder = _feeder(2, group_id=1, edge_type="feeds")
    assert _kind([meta, feeder], 1) == "meta"


def test_informs_only_is_capstone():
    meta = _target(1, group_id=1, group_type="Capstone")
    feeder = _feeder(2, group_id=1, edge_type="informs")
    assert _kind([meta, feeder], 1) == "capstone"


def test_feeds_plus_informs_stays_meta():
    # An informs edge alongside feeds does NOT downgrade to capstone.
    meta = _target(1, group_id=1)
    f1 = _feeder(2, group_id=1, edge_type="feeds")
    f2 = _feeder(3, group_id=1, edge_type="informs")
    assert _kind([meta, f1, f2], 1) == "meta"


def test_all_meta_feeders_is_metameta():
    # A meta whose feeds-feeders are all themselves metas.
    metameta = _target(1, group_id=1)
    sub_a = _target(2, group_id=2)
    sub_b = _target(3, group_id=3)
    # sub_a / sub_b feed the metameta's group on feeds edges (and are metas).
    sub_a["rounds"].append({"id": 1, "is_target_of": False, "edges": ["feeds"], "group_type": "Metapuzzle"})
    sub_b["rounds"].append({"id": 1, "is_target_of": False, "edges": ["feeds"], "group_type": "Metapuzzle"})
    assert _kind([metameta, sub_a, sub_b], 1) == "metameta"


def test_one_non_meta_feeder_keeps_meta():
    # Not every feeds-feeder is a meta -> plain meta, not metameta.
    meta = _target(1, group_id=1)
    sub = _target(2, group_id=2)
    sub["rounds"].append({"id": 1, "is_target_of": False, "edges": ["feeds"], "group_type": "Metapuzzle"})
    plain = _feeder(3, group_id=1, edge_type="feeds")
    assert _kind([meta, sub, plain], 1) == "meta"


def test_runaround_type_wins_over_metameta():
    # Runaround (stored type) outranks metameta even when feeders are all metas.
    ra = _target(1, group_id=1, group_type="Runaround")
    sub = _target(2, group_id=2)
    sub["rounds"].append({"id": 1, "is_target_of": False, "edges": ["feeds"], "group_type": "Metapuzzle"})
    assert _kind([ra, sub], 1) == "runaround"


def test_empty_capstone_falls_back_to_type():
    # A target with no feeders falls back to its stored group type.
    cap = _target(1, group_id=1, group_type="Capstone")
    assert _kind([cap], 1) == "capstone"


def test_empty_meta_falls_back_to_meta():
    meta = _target(1, group_id=1, group_type="Metapuzzle")
    assert _kind([meta], 1) == "meta"
