"""Pure helpers for the unified contribution table (Milestone D).

One table per target group. Each row is [key, source_name, type, content]:
  feeds:<pid>            member name   Feeder       solved answer (blank if unsolved)
  none:<pid>             member name   Group        ""
  informs:<pid>          member name   Information  ""   (bare edge, nothing logged)
  informs:<pid>:<eid>    member name   Information  datum content (one per evidence row)

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
               target (may be empty). Used only for informs members.
    Order: members in the given order; informs evidence in the given order.
    """
    ev_by_source = {}
    for e in evidence:
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
    return rows
