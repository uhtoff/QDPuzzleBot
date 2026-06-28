from bot.store.puzzle_data import PuzzleData


def test_add_edge_tracks_set_and_membership():
    p = PuzzleData()
    p.add_edge(5, "feeds")
    p.add_edge(5, "informs")
    assert p.edges_for(5) == {"feeds", "informs"}
    assert 5 in p.tags
    assert p.tags.count(5) == 1


def test_has_edge():
    p = PuzzleData()
    p.add_edge(5, "feeds")
    assert p.has_edge(5, "feeds") is True
    assert p.has_edge(5, "informs") is False
    assert p.has_edge(9, "feeds") is False


def test_remove_edge_drops_membership_when_empty():
    p = PuzzleData()
    p.add_edge(5, "feeds")
    p.add_edge(5, "informs")
    p.remove_edge(5, "feeds")
    assert p.edges_for(5) == {"informs"}
    assert 5 in p.tags
    p.remove_edge(5, "informs")
    assert p.edges_for(5) == set()
    assert 5 not in p.tags
    assert 5 not in p.tag_edges


def test_edges_for_missing_returns_empty_set():
    assert PuzzleData().edges_for(123) == set()
