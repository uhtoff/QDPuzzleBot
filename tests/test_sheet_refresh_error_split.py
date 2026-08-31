"""SheetRefreshError carries TWO separate id spaces (D9), not one.

failed_group_ids holds tag_group ids whose meta/rollup sheet failed to
refresh -- !rebuild re-renders those, so "rebuild to retry" is correct
advice for this case.

failed_puzzle_ids holds puzzle ids whose move to the solved spreadsheet
failed -- !rebuild never touches tab location, so it cannot retry this one;
the tab is simply still on the main spreadsheet. Before the split, a single
combined id list gave every caller rebuild advice regardless of which kind
of failure occurred, which was actively wrong for the puzzle case.
"""
from bot.core.errors import SheetRefreshError


def test_a_group_only_failure_advises_rebuild():
    exc = SheetRefreshError(failed_group_ids=[5])
    message = str(exc)

    assert "meta sheet" in message
    assert "group ids: [5]" in message
    assert "rebuild to retry" in message
    assert "solved spreadsheet" not in message


def test_a_puzzle_only_failure_does_not_advise_rebuild():
    exc = SheetRefreshError(failed_puzzle_ids=[42])
    message = str(exc)

    assert "puzzle sheet" in message
    assert "puzzle ids: [42]" in message
    assert "still on the main spreadsheet" in message
    # The wrong-advice regression: !rebuild cannot retry a solved-archive move.
    assert "rebuild to retry" not in message


def test_a_mixed_failure_names_both_kinds_separately():
    exc = SheetRefreshError(failed_group_ids=[5, 6], failed_puzzle_ids=[42])
    message = str(exc)

    assert "meta sheet" in message
    assert "puzzle sheet" in message
    assert "group ids: [5, 6]" in message
    assert "puzzle ids: [42]" in message

    group_fragment, _, _ = message.partition("puzzle ids:")
    assert "42" not in group_fragment


def test_plurals_agree_with_each_list_independently():
    exc = SheetRefreshError(failed_group_ids=[5], failed_puzzle_ids=[42, 43])
    message = str(exc)

    assert "1 meta sheet " in message
    assert "2 puzzle sheets " in message


def test_it_still_subclasses_runtimeerror():
    """Load-bearing inheritance, not decoration.

    A dozen sites in bot/web/actions.py and bot/cogs/puzzles.py catch a bare
    `except RuntimeError` around service calls and turn it into a 400 with an
    error slug. Only four of them list `except SheetRefreshError` first. If this
    class stops being a RuntimeError, the rest stop catching it and a handled
    400 becomes an unhandled 500 -- with no other test in the suite going red.
    That is not hypothetical: it happened once during this split.
    """
    assert issubclass(SheetRefreshError, RuntimeError)
