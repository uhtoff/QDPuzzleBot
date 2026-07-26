"""Pure helpers for the unified contribution table (Milestone D).

One table per target group. Each row is [key, source_name, type, content]:
  feeds:<pid>            member name   Feeder       solved answer (blank if unsolved)
  none:<pid>             member name   Group        ""
  informs:<pid>          member name   Information  ""   (bare edge, nothing logged)
  informs:<pid>:<eid>    member name   Information  datum content (one per evidence row)
  informs:free:<eid>     label or —    Information  datum with no source puzzle

Keys are edge-anchored and stable, so the column-A diff survives re-tagging.
"""


def _feeder_answer(puzzle):
    return puzzle.solution.upper() if (puzzle.solution) else ""


def contribution_rows(tag_group_id, members, evidence):
    """Build the desired contribution rows for one target group.

    members  : PuzzleData list (everything tagged into tag_group_id), each with
               .tag_edges[tag_group_id] in {"feeds","informs","none"}. The target's
               own 'target' self-edge matches no branch below and yields no row (the
               target heads the table, it is not a contribution of itself).
    evidence : list of dicts {"id","source_puzzle_id","content"} routed to this
               target (may be empty). Used only for informs members. An item
               with source_puzzle_id None belongs to the hunt rather than to
               any one puzzle (optionally carrying a "source_label") and is
               rendered after the member rows instead of being grouped under
               a member.
    Order: members in the given order; informs evidence in the given order;
           sourceless evidence last, in the given order.
    """
    ev_by_source = {}
    sourceless = []
    for e in evidence:
        if e.get("source_puzzle_id") is None:
            sourceless.append(e)
        else:
            ev_by_source.setdefault(e["source_puzzle_id"], []).append(e)

    # Column order: key (hidden), Source (member name), Type, Content.
    rows = []
    for p in members:
        edges = p.edges_for(tag_group_id)
        if not edges:
            # Transitive subtree member (runaround 'subtree' mode): reached via a
            # 'feeds' chain through sub-metas, so it carries no edge to THIS group.
            # The walk only follows 'feeds', so every such member is a feeder answer
            # — render it as one (blank until it solves).
            rows.append([f"feeds:{p.id}", p.name, "Feeder", _feeder_answer(p)])
            continue
        if "feeds" in edges:
            rows.append([f"feeds:{p.id}", p.name, "Feeder", _feeder_answer(p)])
        if "none" in edges:
            rows.append([f"none:{p.id}", p.name, "Group", ""])
        if "informs" in edges:
            data = ev_by_source.get(p.id, [])
            if not data:
                rows.append([f"informs:{p.id}", p.name, "Information", ""])
            else:
                for e in data:
                    rows.append([f"informs:{p.id}:{e['id']}", p.name,
                                 "Information", e["content"]])

    # Evidence with no source puzzle has no member row to hang from — a fact
    # that belongs to the hunt, or (Plan B) one carved out of a canvas image.
    # Its own key namespace keeps it clear of `informs:<pid>`, and the reconciler
    # diffs purely on column A, so these rows survive re-tagging like any other.
    # No `informs` edge exists for them: an edge joins two puzzles, and a picture
    # or a bare observation is not one.
    for e in sourceless:
        rows.append([f"informs:free:{e['id']}", e.get("source_label") or "—",
                     "Information", e["content"]])
    return rows
