# Profile avatar + Admin "view as" — Design

Date: 2026-06-28
Status: approved (design); implementation pending

## Goal

Two related improvements to the dashboard's profile area:

- **A. Discord profile picture** — show the user's real Discord avatar in the
  circular header button instead of the initials fallback.
- **B. Admin "view as"** — let an Admin temporarily downgrade their *view* to
  Organiser or Regular user, to check what those users see. Pure UI preview, not
  a permission change.

## Background / current state

- The avatar is **already wired up** in `frontend/src/components/AppShellLayout.tsx`
  (`avatarUrl()` at lines 19–22, `<Avatar>` at line 51). It builds
  `https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png`.
- It renders initials because `user.id` is a Discord **snowflake** (~19 digits),
  but `User.id` is a JS `number`. Snowflakes exceed `Number.MAX_SAFE_INTEGER`
  (2^53), so the value is silently corrupted in the browser → the CDN URL 404s →
  Mantine `<Avatar>` falls back to the initials children. The avatar **hash** is
  already present (`user.avatar`); only the ID is unusable client-side.
- Permissions are **per-hunt** (per Discord guild). `bot/web/auth.py
  user_can_manage(user_id, guild_snowflake, roles=…)` resolves the user's roles
  in that guild. The hunt-detail endpoint (`bot/web/api.py` ~lines 99–111)
  returns `can_manage` (organiser+, and not archived) and `is_admin` (admin).
  `ADMIN_ROLES = ('Moderator','mod','admin')`; `ORGANISER_ROLES = ADMIN_ROLES +
  ('Organisers',)`.
- The backend independently enforces permissions on every mutating action
  (`require_organiser` / `require_admin` in `bot/web/actions.py`). The frontend
  flags are display hints only.

## A. Discord profile picture

### Approach

The backend holds the exact integer ID and the avatar hash, so it builds the URL
and hands the frontend a ready-made string. This sidesteps the snowflake
precision problem entirely and centralises the Discord CDN format.

### Backend (`bot/web/auth.py`)

- Add a pure helper:

  ```python
  def discord_avatar_url(user_id, avatar_hash, size=64):
      """CDN URL for a user's Discord avatar, or None if they have no custom one.
      Animated avatars (hash prefixed 'a_') are served as .gif."""
      if not avatar_hash:
          return None
      ext = "gif" if str(avatar_hash).startswith("a_") else "png"
      return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size={size}"
  ```

- Where the session user dict is serialized for the client (the `/me` response,
  ~line 76–81, and the user serialization at ~line 65), include
  `"avatar_url": discord_avatar_url(user["id"], user.get("avatar"))`.
- Keep the existing `id` / `username` / `global_name` / `avatar` fields as-is
  (no removal needed; we just stop using `id` for the URL on the client).

### Frontend

- `frontend/src/types.ts`: add `avatar_url: string | null` to `interface User`.
- `frontend/src/components/AppShellLayout.tsx`: replace `avatarUrl(user)` body
  with `return user.avatar_url ?? undefined;` (drop the `user.id` URL build).
  Initials fallback (`displayName.slice(0,2)`) is unchanged and still used when
  `avatar_url` is null.

### Behaviour

- User with a custom avatar → real picture.
- User with no avatar (`avatar` null) → `avatar_url` null → initials (today's
  behaviour, preserved).
- Animated avatar → `.gif`.

## B. Admin "view as" preview

### Non-goals / security note

This is a **frontend visibility preview only**. It does not, and must not, be
treated as a permission boundary: the backend still authorises every action by
the user's real Discord roles. Downgrading the view only hides UI. This is
acceptable because the feature's purpose is "what does a regular user / organiser
*see*", not access control.

No backend changes in Part B.

### Model: global ceiling

A single global selection caps the role the dashboard renders as, applied
everywhere. Effective role on each hunt = `min(real role there, ceiling)`. On a
hunt where the admin is only a regular user anyway, choosing "Organiser" adds
nothing.

- State: `viewAs: 'admin' | 'organiser' | 'regular'`, default `'admin'`.
- **In-memory only** (React state in a context provider). A page reload resets
  to `'admin'` — the safety property that prevents getting stuck downgraded.

### Effective-flags helper

A pure function applies the ceiling to a hunt detail's two flags:

```ts
// 'admin'    -> unchanged
// 'organiser'-> is_admin=false, can_manage kept (if real)
// 'regular'  -> is_admin=false, can_manage=false
function applyViewAs(detail: HuntDetail, viewAs: ViewAs): HuntDetail {
  if (viewAs === "admin") return detail;
  const can_manage = viewAs === "organiser" ? detail.hunt.can_manage : false;
  return { ...detail, hunt: { ...detail.hunt, can_manage, is_admin: false } };
}
```

Because every gated site already reads `detail.hunt.can_manage` /
`detail.hunt.is_admin`, overriding those two fields on the object passed to
children cascades to all of them with no per-component edits. The 12 gated sites
(for reference, not edited): HuntBoard tabs/CreateControls/DragBoard/
ReconcilePanel/AdminSettingsPanel, BoardGroup rebuild + delete-round, PuzzleDrawer
delete, DragBoard card delete, PuzzleTable debug-tag menu.

### Context

- New `frontend/src/context/ViewAsContext.tsx` exposing `{ viewAs, setViewAs }`.
  `App.tsx` already renders `<AppShellLayout>` once, above `<Routes>` (it does not
  unmount on route changes). Mount `<ViewAsProvider>` in `App.tsx` wrapping
  `<AppShellLayout>`, so both the layout (dropdown + banner) and the routed pages
  (`HuntList`, `HuntBoard`) consume the same state. State lives in React memory →
  survives SPA navigation between pages, resets on a full reload.

### Wiring

- **`HuntBoard.tsx`**: after fetching `data`, compute
  `const detail = applyViewAs(data, viewAs)` and use `detail` for everything it
  passes down and gates on (tabs, panels, board, table).
- **`HuntList.tsx`**: the global "New hunt" button is gated on `isAdmin`
  (`useAdminGuilds().length > 0`). Make it also respect the ceiling:
  `isAdmin && viewAs === 'admin'`.

### UI

- **Dropdown** (`AppShellLayout.tsx`): when the user is admin in ≥1 guild
  (`useAdminGuilds()`), the avatar menu shows an inline "VIEW AS" section:

  ```
  ┌─────────────────────────────┐
  │ (avatar)  russ              │
  │           Admin             │
  ├─────────────────────────────┤
  │ VIEW AS                     │
  │  ✓  Admin (you)             │
  │     Organiser               │
  │     Regular user            │
  ├─────────────────────────────┤
  │  ⎋  Log out                 │
  └─────────────────────────────┘
  ```

  Mantine `Menu.Label` + three `Menu.Item`s; the active one shows a check
  (`IconCheck`). Selecting an item calls `setViewAs(...)`. Non-admins see the
  menu without the VIEW AS section (unchanged).

- **Banner** (`AppShellLayout`, under the header): rendered only while
  `viewAs !== 'admin'`:

  ```
  👁 Viewing as: Regular user   [ Restore ]
  ```

  Minimal Mantine `Alert`/`Group` styled distinctly (e.g. amber). `[Restore]`
  calls `setViewAs('admin')`. The level label maps `organiser`→"Organiser",
  `regular`→"Regular user".

## Testing

- Backend: unit test for `discord_avatar_url` — normal hash → `.png?size=64`,
  animated `a_…` → `.gif`, `None`/empty hash → `None`.
- Frontend: no component test runner exists today. Verify with
  `npx tsc --noEmit` and manual check. Optionally add a tiny pure-function test
  for `applyViewAs` (admin no-op; organiser drops admin only; regular drops
  both) if a lightweight test path is introduced — otherwise covered by review.

## Phasing

1. **Phase A — avatar fix**: backend helper + `avatar_url` field; frontend type +
   `avatarUrl()`. Self-contained; shippable alone.
2. **Phase B — view as**: context, `applyViewAs`, dropdown section, banner,
   HuntBoard/HuntList wiring.

## Acceptance criteria

- A1: A user with a custom Discord avatar sees their picture in the header
  button; animated avatars render; users without an avatar still see initials.
- A2: No reliance on `user.id` for the avatar URL; `tsc --noEmit` clean.
- B1: An admin can pick Admin / Organiser / Regular from the avatar dropdown;
  the dropdown section is absent for non-admins.
- B2: Choosing Organiser hides admin-only UI but keeps organiser controls;
  choosing Regular hides both admin and organiser UI, across HuntBoard and the
  HuntList "New hunt" button.
- B3: A banner shows while downgraded and a reload restores the real role.
- B4: No backend authorisation change; mutations remain gated server-side.

## Constraints / things not to change

- Don't alter the 12 gated components' internal logic — only the flags they
  receive.
- Don't change backend permission enforcement or role constants.
- Keep the initials fallback intact.
