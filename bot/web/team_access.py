"""Assembly for the Team servers page: who is on a hunt's team server, who was
invited and hasn't arrived, and the access history. Pure functions over plain
rows and discord-like objects, so they test without Quart.

Nothing here touches an invite code: invite rows are expected without one."""

from bot.core import role_panel


def iso_z(dt):
    """ISO-8601 UTC with a trailing Z (stored DATETIMEs are UTC-naive)."""
    return dt.replace(microsecond=0).isoformat() + "Z" if dt else None


def hub_team_hunts(hub, rows):
    """Keep the (hunt, snowflake) rows whose roster role exists on *hub*."""
    return [(h, s) for h, s in rows
            if h.roster_role_id and hub.get_role(int(h.roster_role_id)) is not None]


def members_now(team_guild, hunt):
    """Every non-bot member of the team server, flagged team role / mod, sorted
    mod first, then team role, then neither, then by name."""
    mod_ids = {r.id for r in role_panel.admin_roles(team_guild)}
    team_role = team_guild.get_role(int(hunt.role_id)) if hunt.role_id else None
    out = []
    for m in team_guild.members:
        if m.bot:
            continue
        held = {r.id for r in m.roles}
        out.append({
            "user_id": str(m.id),
            "name": m.display_name,
            "team_role": team_role is not None and team_role.id in held,
            "mod": bool(held & mod_ids),
        })
    out.sort(key=lambda d: (0 if d["mod"] else 1 if d["team_role"] else 2,
                            d["name"].lower()))
    return out


def waiting(invite_rows, here_ids):
    """Latest invite per user that hasn't been joined and whose user isn't on the
    server now, newest first. Rows have user_id/created_at/joined_at, no code."""
    latest = {}
    for r in invite_rows:  # oldest first, so later rows win
        latest[int(r["user_id"])] = r
    out = [r for uid, r in latest.items()
           if r["joined_at"] is None and uid not in here_ids]
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return [{"user_id": str(r["user_id"]), "invited_at": iso_z(r["created_at"])}
            for r in out]


def history(log_rows, name_of):
    """Access log newest first; actor_name is name_of(actor_id) or the id string."""
    out = []
    for r in reversed(list(log_rows)):
        actor = r.get("actor_id")
        actor_name = None
        if actor is not None:
            actor_name = name_of(int(actor)) or str(actor)
        out.append({
            "at": iso_z(r["created_at"]),
            "user_id": str(r["user_id"]),
            "name": r.get("username") or str(r["user_id"]),
            "action": r["action"],
            "via": r.get("via"),
            "actor_id": str(actor) if actor is not None else None,
            "actor_name": actor_name,
        })
    return out
