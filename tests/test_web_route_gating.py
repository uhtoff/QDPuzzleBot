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
# resolve to no gate at all — see test_ungated_routes_are_exactly_the_rest — and
# must still be named in one of the two ungated lists below, so that a route can
# never be merely unmentioned (test_every_route_is_accounted_for).
EXPECTED = {
    # --- admin ---------------------------------------------------------------
    ("POST", "/hunts"): "admin",
    ("DELETE", "/hunts/<uid>"): "admin",
    ("PATCH", "/hunts/<uid>/settings"): "admin",
    ("POST", "/hunts/<uid>/archive"): "admin",
    ("POST", "/hunts/<uid>/archive-to-hub"): "admin",
    ("POST", "/hunts/<uid>/complete"): "admin",
    ("POST", "/hunts/<uid>/role"): "admin",
    ("POST", "/hunts/<uid>/solved-sheet"): "admin",
    ("PATCH", "/hunts/<uid>/sheet-mode"): "admin",
    ("POST", "/hunts/<uid>/voice-cleanup"): "admin",
    ("GET", "/guilds/<int:guild_id>/settings"): "admin",
    ("PATCH", "/guilds/<int:guild_id>/settings"): "admin",
    ("GET", "/guilds/<int:guild_id>/team-hunts"): "admin",
    ("GET", "/guilds/<int:guild_id>/team-hunts/<uid>"): "admin",
    ("POST", "/guilds/<int:guild_id>/team-hunts/<uid>/complete"): "admin",
    ("POST", "/guilds/<int:guild_id>/multi-team/<int:mt_id>/archive"): "admin",
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
    # Containers are organiser, content is member. Creating or deleting a canvas
    # uploads or destroys a shared image everyone works on; drawing on it does
    # not. The six region/retile/ocr-preview routes are therefore NOT here — see
    # UNGATED_BY_DECISION below.
    ("POST", "/hunts/<uid>/canvases"): "organiser",
    ("PATCH", "/canvases/<int:canvas_id>"): "organiser",
    ("DELETE", "/canvases/<int:canvas_id>"): "organiser",
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
    ("POST", "/puzzles/<int:puzzle_id>/voice"),     # any hunt member, like !voice
    ("POST", "/puzzles/<int:puzzle_id>/unsolve"),
    # Team-server setup: gated by _team_setup_ctx (server owner OR Admin tier
    # there), a custom gate this census cannot resolve to a tier. A fresh clone's
    # owner holds no `mod` yet, so require_admin would lock them out. Pinned by
    # tests/test_setup_web.py::test_owner_without_mod_can_set_up / test_stranger_refused.
    ("GET", "/guilds/<int:guild_id>/setup"),
    ("POST", "/guilds/<int:guild_id>/setup"),
    # Canvas CONTENT, as against the canvas itself. These six were listed as
    # organiser until 2026-08-31 and never were: they reach require_organiser only
    # through _canvas_ctx's `if require_manage:`, which they do not set, and the
    # old resolver could not see the condition. The design is deliberate and
    # written down in three places — the docstrings on retile_canvas ("a stuck
    # canvas blocks everyone hunting, and retiling changes no content") and
    # create_puzzle_from_region ("not organiser-gated, because creating a puzzle
    # from the board is not either"), and on
    # tests/test_canvas_web.py::test_a_non_organiser_can_draw ("everyone marks the
    # picture up during a hunt"). Read those before "correcting" these back.
    ("POST", "/canvases/<int:canvas_id>/regions"),
    ("PATCH", "/canvases/<int:canvas_id>/regions/<int:region_id>"),
    ("DELETE", "/canvases/<int:canvas_id>/regions/<int:region_id>"),
    ("POST", "/canvases/<int:canvas_id>/retile"),
    ("POST", "/canvases/<int:canvas_id>/ocr-preview"),
    ("POST", "/canvases/<int:canvas_id>/regions/<int:region_id>/create-puzzle"),
    # Extension reply: no dashboard tier — gated per request by hunt visibility and
    # the member's LIVE Discord view_channel + send_messages on that channel (not
    # timed out), because the webhook bypasses Discord's own checks. Pinned by
    # tests/test_channel_reply_web.py.
    ("POST", "/channels/<int:channel_id>/messages"),
]

# The rest of the open surface: reads, and the writes nobody has argued about.
# Flat and unannotated on purpose — UNGATED_BY_DECISION above carries the reasons
# for the routes a tightening pass would start from, and this list carries only
# completeness. Together with EXPECTED the three lists must name EVERY route, so
# a new one cannot land ungated and vanish into an unnamed residue: adding a
# route means choosing a list, which is the decision this file exists to force.
UNGATED_REMAINDER = [
    ("DELETE", "/evidence/<int:item_id>"),
    ("DELETE", "/media/<int:media_id>/markup/<int:mark_id>"),
    ("DELETE", "/puzzles/<int:puzzle_id>/images/<int:image_id>"),
    ("DELETE", "/puzzles/<int:puzzle_id>/notes/<int:index>"),
    ("GET", "/attachments/<int:attachment_id>"),
    ("GET", "/channels/<int:channel_id>/messages"),
    ("GET", "/evidence"),
    ("GET", "/extension/csrf"),
    ("GET", "/extension/ws-ticket"),
    ("GET", "/guilds"),
    ("GET", "/hunts"),
    ("GET", "/hunts/<uid>"),
    ("GET", "/hunts/<uid>/canvases"),
    ("GET", "/hunts/<uid>/puzzles"),
    ("GET", "/media/<int:media_id>"),
    ("GET", "/media/<int:media_id>/markup"),
    ("GET", "/media/<int:media_id>/tiles/<int:z>/<int:y>/<int:x>"),
    ("GET", "/puzzles/<int:puzzle_id>/images"),
    ("GET", "/puzzles/by-sheet"),
    ("GET", "/rollups/<int:tag_group_id>/answers"),
    ("PATCH", "/evidence/<int:item_id>"),
    ("PATCH", "/media/<int:media_id>/markup/<int:mark_id>"),
    ("PATCH", "/puzzles/<int:puzzle_id>"),
    ("PATCH", "/puzzles/<int:puzzle_id>/images"),
    ("POST", "/evidence"),
    ("POST", "/hunts/<uid>/evidence/ocr-preview"),
    ("POST", "/hunts/<uid>/media"),
    ("POST", "/hunts/<uid>/sheets"),
    ("POST", "/media/<int:media_id>/markup"),
    ("POST", "/puzzles/<int:puzzle_id>/images"),
    ("POST", "/puzzles/<int:puzzle_id>/link"),
    ("POST", "/puzzles/<int:puzzle_id>/notes"),
]


_UNKNOWN = object()


def _dec_name(node):
    if isinstance(node, ast.Call):
        return _dec_name(node.func)
    if isinstance(node, ast.Attribute):
        return f"{_dec_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Name):
        return node.id
    return "?"


def _const(node):
    """A literal argument's value, or _UNKNOWN if it is not a literal."""
    return node.value if isinstance(node, ast.Constant) else _UNKNOWN


def _signature(node):
    """(positional param names, {param: default}) for a function definition."""
    a = node.args
    order = [p.arg for p in (*a.posonlyargs, *a.args)]
    defaults = {}
    for name, default in zip(order[len(order) - len(a.defaults):], a.defaults):
        defaults[name] = _const(default)
    for param, default in zip(a.kwonlyargs, a.kw_defaults):
        defaults[param.arg] = _UNKNOWN if default is None else _const(default)
    return order, defaults


def _guard(test, order):
    """(param, expected truthiness) if `test` is `param` / `not param`, else None."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _guard(test.operand, order)
        return (inner[0], not inner[1]) if inner else None
    if isinstance(test, ast.Name) and test.id in order:
        return (test.id, True)
    return None


def _calls(node, order):
    """Every call in a function, as (name, guards, positional args, keyword args).

    `guards` is the chain of `if <own parameter>:` tests the call sits under —
    what makes a parameterised gate like _canvas_ctx's resolvable per call site.
    """
    found = []

    def visit(child, guards):
        if isinstance(child, ast.Call):
            found.append((
                _dec_name(child.func),
                guards,
                [_const(a) for a in child.args],
                {kw.arg: _const(kw.value) for kw in child.keywords if kw.arg},
            ))
        if isinstance(child, ast.If):
            cond = _guard(child.test, order)
            visit(child.test, guards)
            body = guards + (cond,) if cond else guards
            orelse = guards + ((cond[0], not cond[1]),) if cond else guards
            for stmt in child.body:
                visit(stmt, body)
            for stmt in child.orelse:
                visit(stmt, orelse)
            return
        for grandchild in ast.iter_child_nodes(child):
            visit(grandchild, guards)

    for child in ast.iter_child_nodes(node):
        visit(child, ())
    return found


def _resolve():
    """{(method, rule): tier or None} for every route across the web modules.

    Conditional gates are honoured. `_canvas_ctx(canvas_id, require_manage=False)`
    calls require_organiser only under `if require_manage:`, so a naive "does
    require_organiser appear anywhere in this function" pass reports every one of
    its callers as organiser — including the six region/retile/ocr routes that are
    deliberately open, and, worse, it keeps reporting update_canvas and
    delete_canvas as organiser after someone flips their require_manage=True to
    False. The guardrail could not see the one regression it exists to catch.

    So each call records the chain of `if <parameter>:` tests it sits under, and a
    guarded gate counts only when the call site's own arguments (or the callee's
    defaults) make that chain true.

    What it still cannot see:
      * a guard on anything other than a bare parameter — `if flags["manage"]:`,
        `if level > 1:`, `if not ctx["public"]:` — is not evaluated, and the gate
        under it is assumed to fire. Same for try/except, `and`/`or`, and early
        `return`s that skip a later gate.
      * an argument that is not a literal (`require_manage=want_manage`) is
        _UNKNOWN, and its guard is likewise assumed to hold.
      * a gate reached through something that is not a direct call by name —
        a decorator that gates, a dict/registry dispatch, getattr — is invisible,
        as it was before.
    Both assumptions err towards reporting a gate that may not run, i.e. towards
    the map claiming MORE protection than the code has. That is the direction this
    fix narrows, not one it removes: keep route-level tests as the real check.
    """
    funcs, routes = {}, []
    for source in _SOURCES:
        tree = ast.parse(pathlib.Path(source).read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            order, defaults = _signature(node)
            funcs[node.name] = (order, defaults, _calls(node, order))
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

    def value_of(param, order, defaults, args, kwargs):
        if param in kwargs:
            return kwargs[param]
        if param in order and order.index(param) < len(args):
            return args[order.index(param)]
        return defaults.get(param, _UNKNOWN)

    def fires(guards, order, defaults, args, kwargs):
        for param, expected in guards:
            actual = value_of(param, order, defaults, args, kwargs)
            if actual is not _UNKNOWN and bool(actual) is not expected:
                return False
        return True

    def gate(name, seen=(), args=(), kwargs=None):
        if name in seen or name not in funcs:
            return None
        order, defaults, calls = funcs[name]
        kwargs = kwargs or {}
        live = [c for c in calls if fires(c[1], order, defaults, args, kwargs)]
        reached = {c[0] for c in live}
        if "require_admin" in reached:
            return "admin"
        if "require_organiser" in reached:
            return "organiser"
        best = None
        for callee, _guards, callee_args, callee_kwargs in live:
            found = gate(callee, seen + (name,), callee_args, callee_kwargs)
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


def test_every_route_is_accounted_for(gates):
    """And no route may arrive ungated and slip by unnamed.

    The gated half above only pins routes that HAVE a gate, so a new mutating
    route landing with none simply joined the residue and the suite stayed green.
    Pinning the open half too means a new route fails here until someone puts it
    in a list on purpose.
    """
    named = set(EXPECTED) | set(UNGATED_BY_DECISION) | set(UNGATED_REMAINDER)
    unnamed = set(gates) - named
    assert not unnamed, (
        "new route(s) not in any list — add to EXPECTED with a tier, or to an "
        f"ungated list, deliberately: {sorted(unnamed)}"
    )
    stale = named - set(gates)
    assert not stale, f"listed route(s) no longer exist — drop them: {sorted(stale)}"
