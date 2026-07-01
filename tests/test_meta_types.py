"""CREATE_META_TYPES is the single source of truth for the create-meta type
list offered by the dashboard dropdown (exposed via the hunt-detail API). It
must stay derived from TYPE_DEFAULTS + the input aliases so the UI can't drift
from what create_meta actually accepts."""
from bot.core.puzzle_service import (
    CREATE_META_TYPES,
    GROUP_TYPE_INPUT_ALIASES,
    TYPE_DEFAULTS,
)


def test_create_meta_types_ordered_input_labels():
    assert CREATE_META_TYPES == ("Round", "Metapuzzle", "Capstone", "Runaround", "Group")


def test_every_offered_type_is_accepted_by_create_meta():
    # Mirrors create_meta's validation: a label is valid iff it's a stored type
    # or an input alias. No offered label may be unaccepted.
    for label in CREATE_META_TYPES:
        assert label in TYPE_DEFAULTS or label in GROUP_TYPE_INPUT_ALIASES


def test_every_accepted_type_is_offered():
    # Drift guard: adding a type to TYPE_DEFAULTS or an alias must surface it in
    # the dropdown automatically.
    for label in TYPE_DEFAULTS:
        assert label in CREATE_META_TYPES
    for label in GROUP_TYPE_INPUT_ALIASES:
        assert label in CREATE_META_TYPES
