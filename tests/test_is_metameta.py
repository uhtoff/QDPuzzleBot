"""is_metameta keys off real feeds-edge members that are themselves targets of
other groups. With the 'none' target-self-edge model, a target no longer counts
as a feeds-member of its own group, so a plain meta is not misclassified as a
metameta (the bug the 'none' model fixes).
"""
from bot.core.puzzle_service import is_metameta
from tests.conftest import make_puzzle, make_round


def test_target_with_none_self_edge_alone_is_not_metameta():
    # Regression: with the OLD 'feeds' default the target sat in target_puzzle_ids
    # AND carried a 'feeds' edge, so is_metameta returned True for every meta.
    grp = make_round(id=5, target_puzzle_id=7)
    target = make_puzzle(id=7)
    target.tag_edges = {5: "none"}
    assert is_metameta(grp, [grp], [target]) is False


def test_plain_feeder_is_not_metameta():
    # A meta fed by an ordinary (non-target) puzzle is a plain meta.
    grp = make_round(id=5, target_puzzle_id=7)
    target = make_puzzle(id=7)
    target.tag_edges = {5: "none"}
    feeder = make_puzzle(id=8)
    feeder.tag_edges = {5: "feeds"}
    assert is_metameta(grp, [grp], [target, feeder]) is False


def test_feeds_member_that_is_a_target_is_metameta():
    # A feeds-edge member that is itself the target of another group makes this a
    # metameta (it aggregates meta answers, not feeder answers).
    grp = make_round(id=5, target_puzzle_id=7)
    sub = make_round(id=6, target_puzzle_id=8)
    target = make_puzzle(id=7)
    target.tag_edges = {5: "none"}
    submeta = make_puzzle(id=8)        # the target of group 6
    submeta.tag_edges = {5: "feeds"}
    assert is_metameta(grp, [grp, sub], [target, submeta]) is True
