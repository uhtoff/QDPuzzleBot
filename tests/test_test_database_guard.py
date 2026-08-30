"""The suite must refuse to run against a non-test database.

The live-DB test family (test_tag_edges, test_hunt_category_cascade, ...) runs
real INSERT/DELETE statements against whatever `config.database` names. On a
checkout whose config.json points at production, that is a destructive run.
`tests/conftest.py` guards the whole session; these tests drive the guard's
decision directly, without pytest's session machinery or the real config.
"""

import pytest

from tests.conftest import _is_test_database, guard_test_database


@pytest.mark.parametrize("name", ["puzzlebot_test", "qdpuzzlebot_test", "some_other_test"])
def test_names_ending_in_test_are_accepted(name):
    assert _is_test_database(name) is True


@pytest.mark.parametrize(
    "name",
    ["puzzlebot", "puzzlebot_prod", "puzzlebot_test_live", "testdb", "", None],
)
def test_other_names_are_refused(name):
    assert _is_test_database(name) is False


@pytest.mark.parametrize("name", ["puzzlebot_test", "qdpuzzlebot_test"])
def test_guard_passes_for_a_test_database(name):
    guard_test_database(name)


@pytest.mark.parametrize("name", ["puzzlebot", "puzzlebot_prod"])
def test_guard_refuses_a_production_looking_database(name):
    with pytest.raises(pytest.UsageError) as excinfo:
        guard_test_database(name)
    message = str(excinfo.value)
    assert name in message, "the refusal must name the offending database"
    assert "puzzlebot_test" in message, "the refusal must say what to point config.json at"


@pytest.mark.parametrize("name", ["", None])
def test_guard_refuses_an_unconfigured_database(name):
    with pytest.raises(pytest.UsageError) as excinfo:
        guard_test_database(name)
    assert "config.json" in str(excinfo.value)
