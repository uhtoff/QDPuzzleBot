# Hunt role gating — design

Date: 2026-07-04
Status: approved (pending spec review)

## Goal

Make a hunt's team role actually gate the hunt in **both** places it should:

1. **Discord** — apply a permission overwrite so only the team role (plus the bot)
   can see the hunt's categories/channels. Today this is done **manually** by an
   admin on the initial hunt category; nothing in code applies it.
2. **Web dashboard** — already works today via `hunt.role_id` and
   `hunt_visible` (`bot/web/auth.py:211-228`). Unchanged in intent, but we stop
   creating the footgun that silently breaks it (an empty, unpopulated role).

Plus: give hunts created **open** (no role) a way to be gated later, since there
is currently **no post-creation path** to set `hunt.role_id`.

## Background — what happens today

`PuzzleService.create_hunt` (`bot/core/puzzle_service.py:2405-2488`):

- Parses `role_name`, looks the role up by **name**
  (`discord.utils.get(guild.roles, name=role_name)`).
- If not found, **creates a brand-new role** — but **never adds any member to
  it** (there is no `add_roles` anywhere in the bot). The role is created empty.
- Stores `new_hunt.role_id = team_role.id`.
- **Never applies any Discord permission overwrite** (grep for `set_permissions`
  / `overwrites=` / `PermissionOverwrite` returns zero non-test hits).

Consequences:

- The role name the user asked for is not reflected on the Discord category
  (the reported symptom).
- An auto-created empty role makes the hunt visible to **nobody** on the web
  (`hunt_visible` needs the user to hold `role_id`; nobody holds an empty role;
  there is no admin bypass) and — once we add Discord overwrites — visible to
  nobody in Discord either. An empty gating role = an invisible hunt.

Web visibility for a **role-less** hunt (`role_id == 0`): visible to **any
guild member** (the `# legacy hunt` branch in `hunt_visible`). This is a
legitimate "open hunt" state and stays first-class.

## Scope

**In scope**

- Part 1: creation-time role gating — add creator to the role, apply Discord
  overwrites, roll back cleanly on failure.
- Part 2: post-creation "add role to an open hunt" — web route **and** bang
  command — applying web gating + retroactive Discord overwrites.

**Out of scope**

- Re-assigning / changing the role on an already-gated hunt (`role_id != 0`).
  Rejected with a clear error for now.
- Retrofitting existing hunts in bulk.
- Expanding the bang `!hunt` parser's input surface (username/password/etc.).
- Adding anyone other than the creator to the role.

## Relevant files

- `bot/core/puzzle_service.py` — `create_hunt` (service seam for both paths).
- `bot/cogs/puzzles.py` — `!hunt` command + `Puzzles.create_hunt` wrapper; new
  bang command for Part 2.
- `bot/web/actions.py` — `POST /hunts` create route; new route for Part 2.
- `bot/web/auth.py` — `hunt_visible` (read-only reference; unchanged).
- `bot/core/discord_channels.py` — `SOLVE_CATEGORY`, `SOLVE_DIVIDER`.
- `bot/store/hunt_data.py` — `role_id` field.
- `tests/test_create_hunt_service.py` — extend.

## Design

### The overwrite

When a hunt is gated to `team_role`, the overwrite applied to a **category** is:

```python
overwrites = {
    guild.default_role: PermissionOverwrite(view_channel=False),  # @everyone hidden
    team_role:          PermissionOverwrite(view_channel=True),   # team sees it
    guild.me:           PermissionOverwrite(view_channel=True),   # bot keeps access
}
```

- Only `view_channel` is toggled. Participation perms (send/read history) come
  from the members' base roles as they do today.
- `guild.me` allow is belt-and-suspenders: denying `@everyone` must never lock
  the bot out of channels it manages.
- Child channels **synced** to a category inherit live — we set overwrites on
  **categories**, not individual channels.

### Part 1 — creation-time gating

New parameter on `create_hunt`: `creator: discord.Member | None = None`.

Role resolution runs **first, before any category/sheet/DB work**, so a failure
leaves zero half-built artifacts:

```
if role_name and creator is not None:
    team_role = find role by name
    role_was_created = False
    if not found:
        team_role = create role
        role_was_created = True

    if team_role in creator.roles:
        pass                                  # already a member -> gating stays
    else:
        try:
            await creator.add_roles(team_role, reason="hunt-creator")
        except discord.HTTPException:         # e.g. role above bot, no Manage Roles
            if role_was_created:
                try: await team_role.delete(reason="hunt-create rollback")
                except discord.HTTPException: log warning
            team_role = None                  # drop gating -> OPEN hunt
elif role_name and creator is None:
    team_role = None                          # can't guarantee visibility -> OPEN
```

Rules captured:

- **Never gate with a role the creator isn't in.** On any add failure (or an
  unresolvable creator) we set `team_role = None`, so `role_id` stays 0 and no
  overwrite is applied — the hunt is created **open**, never invisible.
- **Clean up only our own mess.** Delete the role *only* if we created it in
  this call; a pre-existing ("already present") role is left untouched.
- **Just the creator** is added, and only if not already a member.

Category creation then passes `overwrites=` (only when `team_role` is set):

- Main hunt category: `guild.create_category(category_name, overwrites=overwrites)`.
- `general` and the `SOLVE_DIVIDER` channel are synced children → inherit.
- The separate `<name>-solved` category exists **only when
  `SOLVE_CATEGORY` is True** (default is False). When it exists, it also gets
  `overwrites=`; capture its return value (currently discarded at
  `puzzle_service.py:2442-2444`).
- Future round categories are made via `ref_category.clone(...)` → inherit the
  overwrites at clone time. No extra work.

### Part 1 — plumbing the creator

- **Bang:** `!hunt` passes `ctx.author`; `Puzzles.create_hunt` gains a member
  arg and forwards it as `creator=`.
- **Web:** `POST /hunts` resolves the member from the session:
  `guild.get_member(int(current_user()["id"]))`, falling back to
  `await guild.fetch_member(...)`; `None` → passed through as `creator=None`
  (→ open hunt).

### Part 1 — surfacing a dropped gating

No transient flags. The entry point *asked* for a role, so after creation:

> `role_name` was given **and** `new_hunt.role_id == 0` ⇒ gating was dropped.

- Bang: append `⚠️ Created the hunt, but couldn't add you to role 'Team A' —
  the hunt is open (ungated). Fix the role in Discord, then re-gate.`
- Web: return `{"uid": ..., "warning": "role_not_applied"}` for the frontend.

### Part 2 — add a role to an open hunt

Applies **only** to hunts with `role_id == 0`. A hunt already gated
(`role_id != 0`) is rejected (`already_gated`).

New service method, e.g. `PuzzleService.set_hunt_role(guild, hunt, *, role_name,
requester: discord.Member | None)`:

1. Guard: `hunt.role_id != 0` → `RuntimeError("already_gated")`.
2. Resolve the role by name and guarantee the hunt won't become invisible.
   Membership semantics differ from creation to avoid a surprising side effect
   (an admin gating a hunt to "Team A" need not themselves be on Team A):
   - **Role missing** → create it and add the **requester** as the seed member
     (they explicitly asked to gate, mirroring Part 1's creator). If the add
     fails, or the requester is unresolvable → delete the role we just created →
     raise `RuntimeError("role_not_applied")`.
   - **Role exists with ≥1 member** → set gating, **no** membership change (don't
     force the requester in).
   - **Role exists but is empty** → raise `RuntimeError("empty_role")`; gating to
     it would hide the hunt from everyone. The admin populates it (or we get a
     future "add me too" affordance) and retries.
   Rationale for raising rather than silently leaving the hunt open: unlike
   creation (where a role is optional), the caller explicitly asked to gate an
   existing hunt, so a failure must be reported, not swallowed.
3. Set `hunt.role_id`, commit.
4. **Retroactively apply the overwrite** to every existing category:
   - Main hunt category (`hunt.category_id`).
   - `<name>-solved` category **iff `SOLVE_CATEGORY`**.
   - Each existing round/group category (`TagGroupJsonDb.get_all(hunt.id)` →
     `category_id`, channel-layout only; forum-mode groups have no category).
   - Use `category.edit(overwrites={...})` — one request per category.
   - Sequential `await`, tolerant of rate-limit waits.
   - **Partial-failure aware:** collect categories that failed; return them so
     the caller can report "gated N of M categories; these need attention: …".
5. Push board change so dashboards refresh (match existing notifier usage).

**Entry points (both):**

- **Web:** `POST /hunts/<uid>/role` (admin-only, like `POST /hunts`), body
  `{"role_name": "..."}`. Resolves requester from `current_user()`. Returns the
  new gating state + any failed categories.
- **Bang:** `!set_hunt_role <hunt-name>, <role-name>` (admin/mod only, bot
  channel), requester = `ctx.author`. Reports success + any failed categories.

### Rate-limit notes (why this is safe)

- Permission-overwrite edits are **not** the notorious "2 edits / 10 min per
  channel" bucket — that one is for channel **name/topic** changes (already hit
  by this bot's `channel.edit(name=...)`). Overwrite edits use normal buckets.
- Creation-time overwrites are part of the `create_category` call — no extra
  requests.
- Retroactive cost ≈ `rounds + 1 (+1 if SOLVE_CATEGORY)` requests, because we
  edit **categories** and let synced children inherit.
- `max_ratelimit_timeout` is confirmed unset (removed in dc4805e; only the
  diagnostic probe remains in `bot/__init__.py`), so if a large hunt hits the
  global/route limit, discord.py **waits and continues** rather than raising.
  Worst case: slow, not failing.

## Error handling summary

| Situation | Behaviour |
|---|---|
| No `role_name` given | Open hunt (`role_id 0`), no overwrite. Unchanged. |
| `role_name` given, role found, creator already in it | Gate normally. |
| `role_name` given, role found, creator added OK | Gate normally. |
| `role_name` given, role missing, created, creator added OK | Gate normally. |
| Create-time add fails / creator unresolvable | Drop gating → **open** hunt; delete role iff we created it; warn caller. |
| Part 2, role missing, requester add fails / unresolvable | Delete the role we created; **raise** `role_not_applied` (hunt stays open). |
| Part 2, role exists with members | Gate; no membership change. |
| Part 2, role exists but empty | Reject `empty_role` (would hide the hunt); nothing mutated. |
| Part 2 on already-gated hunt (`role_id != 0`) | Reject `already_gated`. |
| Retroactive overwrite fails on some categories | Apply the rest; return the failed list for reporting. |

## Testing

Extend `tests/test_create_hunt_service.py` and add coverage for the new method
(mock guild/member/role/category; assert `add_roles`, `create_role.delete`, and
`create_category(overwrites=...)` / `category.edit(overwrites=...)` calls):

Part 1:
- role found + creator not in it → `add_roles` called, gated, overwrites on
  main category (+ solved iff `SOLVE_CATEGORY`).
- role missing → role created, creator added, gated.
- creator already in role → no `add_roles`, gated.
- add_roles raises + role was created → role deleted, `role_id == 0`, no
  overwrites (open).
- add_roles raises + role pre-existed → role **not** deleted, `role_id == 0`,
  open.
- creator None → open, no role created.
- no role_name → open, unchanged.
- overwrites dict shape: `@everyone` deny, role allow, `guild.me` allow.

Part 2:
- open hunt + existing populated role → `role_id` set, no membership change,
  overwrites applied to main + each round category (+ solved iff
  `SOLVE_CATEGORY`).
- open hunt + missing role → role created, requester seeded, gated.
- open hunt + existing empty role → `empty_role`, nothing mutated.
- already-gated hunt → `already_gated`, nothing mutated.
- missing role, requester add fails → `role_not_applied`, our-own-role cleaned
  up, hunt still open.
- one round category edit fails → others still applied, failed list returned.

## Acceptance criteria

- Creating a hunt with an existing role adds the creator to it and gates the
  Discord categories + web dashboard.
- Creating a hunt with a new role name creates the role, adds the creator, gates
  both.
- A gating failure never yields an invisible hunt and never leaves a stray
  bot-created role.
- Creating a hunt with no role still yields an open, all-guild-visible hunt.
- An open hunt can be gated afterwards from the web dashboard and via
  `!set_hunt_role`, applying both web gating and Discord overwrites to all
  existing categories, reporting any per-category failures.
```
