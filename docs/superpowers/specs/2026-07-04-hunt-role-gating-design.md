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

- Part 1: creation-time role gating.
- Part 2: post-creation "add role to an open hunt" — web route **and** bang
  command.
- A shared role-application routine used by both, with the same two invariants
  (never gate an empty role; confirm reuse of an existing role).

**Out of scope**

- Re-assigning / changing the role on an already-gated hunt (`role_id != 0`).
  Rejected with a clear error for now.
- Retrofitting existing hunts in bulk.
- Expanding the bang `!hunt` parser's input surface (username/password/etc.).
- Adding anyone other than the acting user to the role.

## Relevant files

- `bot/core/puzzle_service.py` — `create_hunt`; new `set_hunt_role`; shared
  role-application helper.
- `bot/cogs/puzzles.py` — `!hunt` + `Puzzles.create_hunt` wrapper; new bang
  command; reuses `_confirm_destructive` (`puzzles.py:3203`).
- `bot/web/actions.py` — `POST /hunts`; new `POST /hunts/<uid>/role`.
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
  from members' base roles as they do today.
- `guild.me` allow is belt-and-suspenders: denying `@everyone` must never lock
  the bot out of channels it manages.
- Child channels **synced** to a category inherit live — we set overwrites on
  **categories**, not individual channels.

### Shared role-application routine

Both entry points resolve/gate a role through one routine with the same two
invariants. It runs **first, before any category/sheet/DB work**, so a failure
leaves zero half-built artifacts. `actor` is the creator (create) or requester
(add-role); it may be `None` if the web layer can't resolve the member.

```
# returns the role to gate with (or None = don't gate)
if not role_name:
    return None                                   # open hunt — unchanged

if actor is None:
    signal "no_actor"                             # can't seed / guarantee visibility

role = discord.utils.get(guild.roles, name=role_name)
if role is not None and not confirm_existing_role:
    signal "existing_role_confirm"                # nothing created yet

created = False
if role is None:
    role = await guild.create_role(name=role_name, colour=..., mentionable=True)
    created = True

if actor not in role.members:
    try:    await actor.add_roles(role, reason="hunt-gating")   # best-effort
    except discord.HTTPException: log warning

if not role.members:                              # would be invisible
    if created:
        try: await role.delete(reason="rollback: gating role left empty")
        except discord.HTTPException: log warning
    signal "empty_after_add"                      # don't gate

return role
```

Two invariants, stated plainly:

- **Never gate a role that would have zero members.** We add the actor to the
  role (this is also the "add the creating user" behaviour requested); if that
  fails *and* the role is otherwise empty, we refuse to gate. A pre-existing
  role that already has members stays gate-able even if we couldn't add the
  actor (it's still visible to its members — we just warn the actor wasn't
  added).
- **Reusing an existing role requires confirmation.** The norm is one fresh role
  per team per hunt, so a name that already resolves is unusual — likely a typo
  or accidental reuse the mod should see. Confirmed reuse also *adds the actor*
  to that role, so the confirm prompt says so.

**Clean up only our own mess:** `role.delete()` fires only when `created` is
true. A pre-existing role is never deleted.

How the two "signals" map to behaviour differs by caller (a role is *optional*
at creation but *explicitly requested* when adding one):

| Signal | `create_hunt` | `set_hunt_role` |
|---|---|---|
| `existing_role_confirm` | prompt; on confirm re-run with `confirm_existing_role=True` | same |
| `no_actor` | gate nothing → **open** hunt | raise `role_not_applied` |
| `empty_after_add` | gate nothing → **open** hunt (+ warn) | raise `role_not_applied` |

### Part 1 — creation-time gating

`create_hunt` gains `creator: discord.Member | None = None` and
`confirm_existing_role: bool = False`. It calls the shared routine; if it returns
a role, the category-creation calls pass `overwrites=`:

- Main hunt category: `guild.create_category(category_name, overwrites=overwrites)`.
- `general` and the `SOLVE_DIVIDER` channel are synced children → inherit.
- The separate `<name>-solved` category exists **only when `SOLVE_CATEGORY` is
  True** (default is False). When it exists it also gets `overwrites=`; capture
  its return value (currently discarded at `puzzle_service.py:2442-2444`).
- Future round categories are made via `ref_category.clone(...)` → inherit the
  overwrites at clone time. No extra work.

**Plumbing:**

- Bang: `!hunt` passes `ctx.author`; `Puzzles.create_hunt` gains a member arg and
  forwards it as `creator=`.
- Web: `POST /hunts` resolves `guild.get_member(int(current_user()["id"]))`,
  falling back to `await guild.fetch_member(...)`; `None` → `creator=None`.

**Surfacing a dropped gating (open outcome):** no transient flags — after
creation, `role_name` given **and** `new_hunt.role_id == 0` ⇒ gating was dropped.
- Bang: `⚠️ Created the hunt, but couldn't gate it to 'Team A' — it's open
  (ungated). Fix the role in Discord, then re-gate.`
- Web: `{"uid": ..., "warning": "role_not_applied"}`.

### Part 2 — add a role to an open hunt

Applies **only** to `role_id == 0` hunts (`already_gated` otherwise). New method
`PuzzleService.set_hunt_role(guild, hunt, *, role_name, requester,
confirm_existing_role=False)`:

1. Guard: `hunt.role_id != 0` → `RuntimeError("already_gated")`.
2. Run the shared routine (requester = actor). Its signals map per the table
   above (`no_actor` / `empty_after_add` → `RuntimeError("role_not_applied")`;
   `existing_role_confirm` → `RuntimeError("existing_role_confirm")`).
3. Set `hunt.role_id`, commit.
4. **Retroactively apply the overwrite** to every existing category:
   - Main hunt category (`hunt.category_id`).
   - `<name>-solved` category **iff `SOLVE_CATEGORY`**.
   - Each existing round/group category (`TagGroupJsonDb.get_all(hunt.id)` →
     `category_id`; channel-layout only — forum-mode groups have no category).
   - `category.edit(overwrites={...})` — one request per category, sequential
     `await`, tolerant of rate-limit waits.
   - **Partial-failure aware:** collect categories that failed and return them so
     the caller can report "gated N of M categories; these need attention: …".
5. Push board change so dashboards refresh (match existing notifier usage).

### Existing-role confirmation (both creation and add-role)

`existing_role_confirm` is a two-step acknowledgement, not an error — reusing the
patterns already in the codebase:

- **Web** (like the convert dialog's return-then-resubmit, `actions.py:984-1017`,
  `1490-1495`): the first call (`POST /hunts` or `POST /hunts/<uid>/role`) whose
  `role_name` matches an existing role returns **HTTP 200**
  `{"needs_confirm": "existing_role", "message": "Role 'Team A' already exists
  (N members). Normally each team gets its own role per hunt — reuse it and add
  you to it?"}`, having created nothing. The frontend shows a confirm button that
  re-submits the same body plus `"confirm_existing_role": true`, which proceeds.
- **Bang** (reuses `_confirm_destructive`, `puzzles.py:3203`): on a name match,
  prompt the same question; a confirm reaction re-runs the operation with
  `confirm_existing_role=True`. Cancel/timeout aborts — creation makes no hunt;
  add-role leaves the hunt open. Nothing is mutated on abort.

A **missing** role name is the expected path: created fresh, actor seeded, gated,
no prompt.

### Entry points (Part 2)

- **Web:** `POST /hunts/<uid>/role` (admin-only, like `POST /hunts`), body
  `{"role_name": "...", "confirm_existing_role"?: bool}`. Requester from
  `current_user()`. Returns the new gating state + any failed categories, or the
  `needs_confirm` response.
- **Bang:** `!set_hunt_role <hunt-name>, <role-name>` (admin/mod only, bot
  channel), requester = `ctx.author`. Prompts on an existing role, then reports
  success + any failed categories.

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
| No `role_name` | Open hunt (`role_id 0`), no overwrite. Unchanged. |
| Role missing → created, actor added | Gate. |
| Role exists, not confirmed | `existing_role_confirm` → prompt (web `needs_confirm` 200 / bang react); nothing created yet. |
| Role exists, confirmed, actor added or already member | Gate; actor is (now) in the role. |
| Role exists, confirmed, actor add fails, role has other members | Gate anyway; warn actor wasn't added. |
| Actor add fails / actor unresolvable, role would be empty | `empty_after_add`/`no_actor` → **create:** open+warn (delete role iff created); **add-role:** raise `role_not_applied` (delete role iff created). |
| Part 2 on already-gated hunt (`role_id != 0`) | Reject `already_gated`. |
| Retroactive overwrite fails on some categories | Apply the rest; return the failed list for reporting. |

## Testing

Extend `tests/test_create_hunt_service.py` and add coverage for the new method /
shared routine (mock guild/member/role/category; assert `add_roles`,
`role.delete`, and `create_category(overwrites=...)` / `category.edit(
overwrites=...)` calls). Key cases:

Shared routine / Part 1:
- no role_name → open, unchanged.
- role missing → created, actor added, gated; overwrites on main (+ solved iff
  `SOLVE_CATEGORY`); overwrites dict shape (`@everyone` deny, role allow,
  `guild.me` allow).
- role exists, `confirm_existing_role=False` → `existing_role_confirm`, nothing
  created.
- role exists, confirmed, actor not a member → actor added, gated.
- role exists, confirmed, actor already a member → no `add_roles`, gated.
- new role, actor add fails → role deleted, open (create) / `role_not_applied`
  (add-role).
- existing role, actor add fails, role has other members → gated, warn.
- actor unresolvable (`None`) with role_name → open (create) / `role_not_applied`
  (add-role); no role created.

Part 2 extras:
- open hunt + existing populated role, confirmed → `role_id` set, overwrites on
  main + each round category (+ solved iff `SOLVE_CATEGORY`).
- already-gated hunt → `already_gated`, nothing mutated.
- one round-category edit fails → others still applied, failed list returned.

## Acceptance criteria

- Creating a hunt with a **new** role name creates the role, adds the creator,
  and gates the Discord categories + web dashboard.
- Creating a hunt with an **existing** role name prompts to confirm reuse; on
  confirm it adds the creator and gates both.
- A gating failure never yields an invisible hunt and never leaves a stray
  bot-created role.
- Creating a hunt with no role still yields an open, all-guild-visible hunt.
- An open hunt can be gated afterwards from the web dashboard and via
  `!set_hunt_role`, applying web gating + Discord overwrites to all existing
  categories, prompting on an existing role, and reporting per-category failures.
