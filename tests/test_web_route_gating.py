"""Inventory of which web routes are permission-gated, and at which tier.

The web counterpart to tests/test_command_gating.py. Individual route tests patch
`require_organiser` and assert the route consults it; this asserts the whole map
at once, so a gate added to the wrong route — or quietly dropped from the right
one — fails here even if nobody wrote a test for that route.

Gates are resolved THROUGH DELEGATION: several routes carry no gate of their own
and hand off to a helper that gates internally (every convert-to-* route goes
through _convert_to_type_handler, and /convert-to-puzzle simply calls
convert_to_feeder). A pass that only reads each route's own body reports those as
ungated, which is how a first draft of this audit produced a false alarm.

Adding a route means adding a line here. That is the point: the tier is a
decision, not an implementation detail.
"""
import ast
import pathlib

import pytest

_SOURCES = ["bot/web/actions.py", "bot/web/api.py"]

# Every gated route, as (method, rule) -> tier. Routes absent from this map must
# resolve to no gate at all — see test_ungated_routes_are_exactly_the_rest.
EXPECTED = {
    # --- admin ---------------------------------------------------------------
    ("POST", "/hunts"): "admin",
    ("DELETE", "/hunts/<uid>"): "admin",
    ("PATCH", "/hunts/<uid>/settings"): "admin",
    ("POST", "/hunts/<uid>/archive"): "admin",
    ("POST", "/hunts/<uid>/role"): "admin",
    ("POST", "/hunts/<uid>/solved-sheet"): "admin",
    ("GET", "/guilds/<int:guild_id>/settings"): "admin",
    ("PATCH", "/guilds/<int:guild_id>/settings"): "admin",
    # --- organiser: group lifecycle -----------------------------------------
    # POST /hunts/<uid>/metas is gated (2026-08-11): creating a tag group is an
    # organiser action like renaming/deleting/retyping one. Creating a PUZZLE is
    # not — POST /hunts/<uid>/puzzles stays open, asserted below.
    ("POST", "/hunts/<uid>/metas"): "organiser",
    ("POST", "/tag_groups/<int:tag_group_id>/change-type"): "organiser",
    ("POST", "/tag_groups/<int:tag_group_id>/rename"): "organiser",
    ("DELETE", "/tag_groups/<int:tag_group_id>"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/rename"): "organiser",
    ("DELETE", "/puzzles/<int:puzzle_id>"): "organiser",
    # --- organiser: convert family (all via _convert_to_type_handler) --------
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-meta"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-capstone"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-runaround"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-metapuzzle"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-group"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-puzzle"): "organiser",
    ("POST", "/puzzles/<int:puzzle_id>/convert-to-feeder"): "organiser",
    ("GET", "/puzzles/<int:puzzle_id>/convert-to-feeder/preview"): "organiser",
    # --- organiser: removals -------------------------------------------------
    ("POST", "/puzzles/<int:puzzle_id>/remove-minimeta"): "organiser",
    ("DELETE", "/puzzles/<int:puzzle_id>/minis/<int:mini_id>"): "organiser",
    # Deleting a working sheet drops the Google tab, so it is gated like the
    # other deletes (2026-08-11); adding one stays open.
    ("DELETE", "/puzzles/<int:puzzle_id>/sheets/<int:sheet_id>"): "organiser",
    ("DELETE", "/hunts/<uid>/sheets/<int:sheet_id>"): "organiser",
    # --- organiser: canvas ---------------------------------------------------
    ("POST", "/hunts/<uid>/canvases"): "organiser",
    ("PATCH", "/canvases/<int:canvas_id>"): "organiser",
    ("DELETE", "/canvases/<int:canvas_id>"): "organiser",
    ("POST", "/canvases/<int:canvas_id>/ocr-preview"): "organiser",
    ("POST", "/canvases/<int:canvas_id>/retile"): "organiser",
    ("POST", "/canvases/<int:canvas_id>/regions"): "organiser",
    ("PATCH", "/canvases/<int:canvas_id>/regions/<int:region_id>"): "organiser",
    ("DELETE", "/canvases/<int:canvas_id>/regions/<int:region_id>"): "organiser",
    ("POST", "/canvases/<int:canvas_id>/regions/<int:region_id>/create-puzzle"): "organiser",
    # --- organiser: maintenance ---------------------------------------------
    ("POST", "/hunts/<uid>/reconcile"): "organiser",
    ("POST", "/rollups/<int:tag_group_id>/rebuild"): "organiser",
    ("GET", "/hunts/<uid>/media/stats"): "organiser",
    ("POST", "/hunts/<uid>/media/sweep"): "organiser",
}

# Deliberately ungated, with the reason. These are the ones a future tightening
# pass would reach for first, so they are named rather than left to the residue.
UNGATED_BY_DECISION = [
    ("POST", "/hunts/<uid>/puzzles"),               # solvers add puzzles as they find them
    ("POST", "/puzzles/<int:puzzle_id>/meta"),      # retag: Tag area, open on the bang edge
    ("POST", "/puzzles/<int:puzzle_id>/make-minimeta"),  # Create area
    ("POST", "/puzzles/<int:puzzle_id>/minis"),     # Create area
    ("POST", "/puzzles/<int:puzzle_id>/sheets"),    # Create area; only removal is gated
    ("POST", "/puzzles/<int:puzzle_id>/solve"),
    ("POST", "/puzzles/<int:puzzle_id>/unsolve"),
]


def _dec_name(node):
    if isinstance(node, ast.Call):
        return _dec_name(node.func)
    if isinstance(node, ast.Attribute):
        return f"{_dec_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return "?"


def _resolve():
    """{(method, rule): tier or None} for every route across the web modules."""
    funcs, routes = {}, []
    for source in _SOURCES:
        tree = ast.parse(pathlib.Path(source).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            funcs[node.name] = {
                _dec_name(c.func) for c in ast.walk(node) if isinstance(c, ast.Call)
            }
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and _dec_name(dec).endswith(".route"):
                    methods = ["GET"]
                    for kw in dec.keywords:
                        if kw.arg == "methods" and isinstance(kw.value, ast.List):
                            methods = [e.value for e in kw.value.elts
                                       if isinstance(e, ast.Constant)]
                    rule = dec.args[0] if dec.args else None
                    if not isinstance(rule, ast.Constant):
                        continue
                    for method in methods:
                        routes.append((node.name, method, rule.value))

    def gate(name, seen=()):
        if name in seen or name not in funcs:
            return None
        calls = funcs[name]
        if "require_admin" in calls:
            return "admin"
        if "require_organiser" in calls:
            return "organiser"
        best = None
        for callee in calls:
            found = gate(callee, seen + (name,))
            if found == "admin":
                return "admin"
            if found == "organiser":
                best = "organiser"
        return best

    return {(method, rule): gate(name) for name, method, rule in routes}


@pytest.fixture(scope="module")
def gates():
    return _resolve()


@pytest.mark.parametrize("key,tier", sorted(EXPECTED.items()))
def test_gated_route_carries_its_tier(gates, key, tier):
    method, rule = key
    assert key in gates, f"{method} {rule} is no longer a route — update EXPECTED"
    assert gates[key] == tier, (
        f"{method} {rule} resolves to {gates[key]!r}, expected {tier!r}"
    )


@pytest.mark.parametrize("key", UNGATED_BY_DECISION)
def test_route_ungated_by_decision(gates, key):
    method, rule = key
    assert key in gates, f"{method} {rule} is no longer a route — update the list"
    assert gates[key] is None, (
        f"{method} {rule} has gained a {gates[key]} gate; it is open by decision "
        "(see .ai/active/current-spec.md D5) — change the decision, not just the code"
    )


def test_ungated_routes_are_exactly_the_rest(gates):
    """No route may quietly acquire a gate, or lose one, without this map moving."""
    actual = {key: tier for key, tier in gates.items() if tier is not None}
    assert actual == EXPECTED
