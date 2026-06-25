"""Pure helpers for the unified contribution table (Milestone D).

One table per target group. Each row is [key, type, source_name, content]:
  feeds:<pid>            Feeder       member name   solved answer (blank if unsolved)
  none:<pid>             Group        member name   ""
  informs:<pid>          Information  member name   ""   (bare edge, nothing logged)
  informs:<pid>:<eid>    Information  member name   datum content (one per evidence row)

Keys are edge-anchored and stable, so the column-A diff survives re-tagging.
"""


def _feeder_answer(puzzle):
    return puzzle.solution.upper() if (puzzle.solution) else ""


def contribution_rows(tag_group_id, members, evidence):
    """Build the desired contribution rows for one target group.

    members  : PuzzleData list (everything tagged into tag_group_id), each with
               .tag_edges[tag_group_id] in {"feeds","informs","none"}.
    evidence : list of dicts {"id","source_puzzle_id","content"} routed to this
               target (may be empty). Used only for informs members.
    Order: members in the given order; informs evidence in the given order.
    """
    ev_by_source = {}
    for e in evidence:
        ev_by_source.setdefault(e["source_puzzle_id"], []).append(e)

    rows = []
    for p in members:
        edge = p.tag_edges.get(tag_group_id)
        if edge == "feeds":
            rows.append([f"feeds:{p.id}", "Feeder", p.name, _feeder_answer(p)])
        elif edge == "none":
            rows.append([f"none:{p.id}", "Group", p.name, ""])
        elif edge == "informs":
            data = ev_by_source.get(p.id, [])
            if not data:
                rows.append([f"informs:{p.id}", "Information", p.name, ""])
            else:
                for e in data:
                    rows.append([f"informs:{p.id}:{e['id']}", "Information",
                                 p.name, e["content"]])
    return rows
