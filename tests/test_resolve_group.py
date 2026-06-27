"""Pure tests for resolve_group — the forgiving group-code/name resolver. No DB."""
from bot.store.tag_group_data import TagGroupData, resolve_group


def _g(name, code, id=0):
    return TagGroupData(name=name, group_code=code, id=id)


# A small pool of metas (group_code is a 6-char string). The "0" entry is a
# non-meta round that must never be matched.
POOL = [
    _g("Alpha Meta", "AB12CD", id=1),
    _g("Beta Round", "EF34GH", id=2),
    _g("Gamma", "IJ56KL", id=3),
    _g("non-meta round", "0", id=4),
]


def test_exact_code_match():
    m = resolve_group("AB12CD", POOL)
    assert m.status == "exact"
    assert m.group.id == 1


def test_code_match_is_case_insensitive_and_trimmed():
    m = resolve_group("  ab12cd ", POOL)
    assert m.status == "exact"
    assert m.group.id == 1


def test_exact_name_match_case_insensitive():
    m = resolve_group("gamma", POOL)
    assert m.status == "name"
    assert m.group.id == 3


def test_unique_substring_match_on_name():
    m = resolve_group("alph", POOL)
    assert m.status == "name"
    assert m.group.id == 1


def test_ambiguous_substring_returns_candidates():
    pool = [_g("Red Meta", "AAA111", 1), _g("Red Herring", "BBB222", 2)]
    m = resolve_group("red", pool)
    assert m.status == "ambiguous"
    assert m.group is None
    assert {g.id for g in m.candidates} == {1, 2}


def test_no_match_returns_none():
    m = resolve_group("zzz", POOL)
    assert m.status == "none"
    assert m.group is None


def test_empty_input_is_empty_status():
    m = resolve_group("   ", POOL)
    assert m.status == "empty"
    assert m.group is None


def test_non_meta_rounds_are_never_matched():
    # The id=4 entry has group_code "0"; searching its name must not resolve it.
    m = resolve_group("non-meta", POOL)
    assert m.status == "none"


def test_exact_code_beats_name_substring():
    # "AB12CD" is a code; even though some name could contain it, code wins cleanly.
    pool = [_g("AB12CD lookalike", "ZZ99ZZ", 1), _g("Real", "AB12CD", 2)]
    m = resolve_group("AB12CD", pool)
    assert m.status == "exact"
    assert m.group.id == 2
