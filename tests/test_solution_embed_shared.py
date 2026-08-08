"""DRY (2026-08-07 review): the partial/change/add embed title and description
were copied verbatim between the bang commands and their web twins, so a wording
change had to be made in two places. #79 already did this for "solved"
(build_solved_embed); this is the same treatment for the other three modes.

The test that matters is the equivalence one: it fails the moment the two
surfaces can disagree, which is the defect the shared builder removes.
"""
import pytest

from bot.core.notifier import build_solution_update_embed


@pytest.mark.parametrize("mode", ["partial", "change", "add"])
def test_both_surfaces_get_identical_wording(mode):
    """Same mode, same emoji, same solution -> byte-identical title and body.
    Only the Follow-up differs, which is deliberate (bang tells you which
    command to use next; the dashboard says it came from the dashboard)."""
    bang = build_solution_update_embed(mode, ":robot:", "ANSWER", "bang follow-up")
    web = build_solution_update_embed(mode, ":robot:", "ANSWER", "web follow-up")
    assert (bang.title, bang.description) == (web.title, web.description)
    assert bang.fields[0].value == "bang follow-up"
    assert web.fields[0].value == "web follow-up"


def test_the_three_modes_are_distinct():
    """One mode proves nothing about a lookup table: a builder that ignored
    `mode` and always produced the partial wording would satisfy the test above
    for all three."""
    titles = {m: build_solution_update_embed(m, "", "A", "f").title
              for m in ("partial", "change", "add")}
    assert titles == {
        "partial": "Partially SOLVED!",
        "change": "PUZZLE SOLUTION UPDATED!",
        "add": "EXTRA PUZZLE SOLUTION ADDED!",
    }


def test_the_solution_reaches_the_description():
    # Guards the formatting seam: a builder that dropped the solution would
    # still pass the title assertions above.
    embed = build_solution_update_embed("partial", "", "PLUGH", "f")
    assert "`PLUGH`" in (embed.description or "")
