# Profile avatar + Admin "view as" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the real Discord avatar in the header (bugfix), and let an Admin preview the dashboard as an Organiser or Regular user.

**Architecture:** Part A moves Discord avatar-URL construction to the backend (it holds the exact snowflake ID), emitting `avatar_url`; the frontend stops building the URL from the precision-lossy `user.id`. Part B is a frontend-only "global ceiling": an in-memory context caps the role the UI renders as, applied by overriding `can_manage`/`is_admin` on the hunt detail passed to children — so all existing gates cascade with no per-component edits. No backend change in Part B; the server still authorises every mutation.

**Tech Stack:** Python/Quart backend (`bot/web`), React 18 + TypeScript + Vite + Mantine frontend (`frontend/src`). Backend tests: `pipenv run python -m pytest`. Frontend has no component test runner — verify with `npx tsc --noEmit`.

**Spec:** `docs/superpowers/specs/2026-06-28-profile-avatar-and-view-as-design.md`

---

## File Structure

- `bot/web/auth.py` — add `discord_avatar_url()` helper; include `avatar_url` in the `/me` user payload.
- `tests/test_auth_avatar.py` (new) — unit tests for `discord_avatar_url`.
- `frontend/src/types.ts` — add `avatar_url` to `User`.
- `frontend/src/components/AppShellLayout.tsx` — use `avatar_url`; add "View as" menu section + banner.
- `frontend/src/context/ViewAsContext.tsx` (new) — `ViewAs` type, context, provider, `useViewAs`, `applyViewAs`.
- `frontend/src/App.tsx` — mount `<ViewAsProvider>` around `<AppShellLayout>`.
- `frontend/src/pages/HuntBoard.tsx` — apply `applyViewAs` to the fetched detail.
- `frontend/src/pages/HuntList.tsx` — gate "New hunt" on the ceiling too.

---

## Phase A — Discord avatar fix

### Task A1: Backend `discord_avatar_url` helper

**Files:**
- Modify: `bot/web/auth.py`
- Test: `tests/test_auth_avatar.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth_avatar.py`:

```python
from bot.web.auth import discord_avatar_url


def test_static_avatar_is_png_with_size():
    url = discord_avatar_url(123456789012345678, "abc123")
    assert url == "https://cdn.discordapp.com/avatars/123456789012345678/abc123.png?size=64"


def test_animated_avatar_is_gif():
    url = discord_avatar_url(123456789012345678, "a_deadbeef")
    assert url == "https://cdn.discordapp.com/avatars/123456789012345678/a_deadbeef.gif?size=64"


def test_no_avatar_returns_none():
    assert discord_avatar_url(123456789012345678, None) is None
    assert discord_avatar_url(123456789012345678, "") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `timeout 120 pipenv run python -m pytest tests/test_auth_avatar.py -q > /tmp/test.log 2>&1; echo exit=$?; tail -n 15 /tmp/test.log`
Expected: FAIL — `ImportError: cannot import name 'discord_avatar_url'`.

- [ ] **Step 3: Write minimal implementation**

In `bot/web/auth.py`, add near the other session/visibility helpers (after `current_user()`, ~line 108):

```python
def discord_avatar_url(user_id, avatar_hash, size=64):
    """CDN URL for a user's Discord avatar, or None if they have no custom one.

    Animated avatars (hash prefixed 'a_') are served as .gif. The full integer
    user_id is used here on the server because a Discord snowflake exceeds JS's
    safe-integer range — the browser cannot rebuild this URL reliably.
    """
    if not avatar_hash:
        return None
    ext = "gif" if str(avatar_hash).startswith("a_") else "png"
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.{ext}?size={size}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `timeout 120 pipenv run python -m pytest tests/test_auth_avatar.py -q > /tmp/test.log 2>&1; echo exit=$?; tail -n 15 /tmp/test.log`
Expected: `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add bot/web/auth.py tests/test_auth_avatar.py
git commit -m "feat(web): discord_avatar_url helper (server-side avatar URL)"
```

### Task A2: Emit `avatar_url` from `/me`

**Files:**
- Modify: `bot/web/auth.py` (the `/me` route, ~lines 76–81)

- [ ] **Step 1: Update the `/me` response**

The session stores `id` (the exact int), `username`, `global_name`, `avatar`. Build the
user payload with an added `avatar_url` instead of returning the raw session dict.
Replace the body of `me()`:

```python
@auth_bp.route("/me")
async def me():
    user = session.get("user")
    if not user:
        return jsonify({"authenticated": False}), 401
    payload = {
        **user,
        "avatar_url": discord_avatar_url(user["id"], user.get("avatar")),
    }
    return jsonify({"authenticated": True, "user": payload})
```

- [ ] **Step 2: Verify the suite still imports/passes for web auth**

Run: `timeout 150 pipenv run python -m pytest tests/test_web_audit.py tests/test_auth_avatar.py -q > /tmp/test.log 2>&1; echo exit=$?; tail -n 15 /tmp/test.log`
Expected: all pass (no regression; route still importable).

- [ ] **Step 3: Lint**

Run: `pipenv run ruff check bot/web/auth.py`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add bot/web/auth.py
git commit -m "feat(web): include avatar_url in /api/auth/me payload"
```

### Task A3: Frontend uses `avatar_url`

**Files:**
- Modify: `frontend/src/types.ts` (the `User` interface, ~lines 8–13)
- Modify: `frontend/src/components/AppShellLayout.tsx` (`avatarUrl`, lines 19–22)

- [ ] **Step 1: Add `avatar_url` to the User type**

In `frontend/src/types.ts`, extend `interface User`:

```ts
export interface User {
  id: number;
  username: string | null;
  global_name: string | null;
  avatar: string | null;
  avatar_url: string | null;
}
```

- [ ] **Step 2: Use it in AppShellLayout**

Replace the `avatarUrl` function body (lines 19–22):

```ts
function avatarUrl(user: User) {
  return user.avatar_url ?? undefined;
}
```

(The initials fallback in `<Avatar>` is unchanged and still used when `avatar_url` is null.)

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/AppShellLayout.tsx
git commit -m "fix(dashboard): render Discord avatar via server avatar_url"
```

---

## Phase B — Admin "view as"

### Task B1: ViewAs context + `applyViewAs` helper

**Files:**
- Create: `frontend/src/context/ViewAsContext.tsx`

- [ ] **Step 1: Create the context module**

```tsx
import { createContext, useContext, useState, type ReactNode } from "react";
import type { HuntDetail } from "../types";

export type ViewAs = "admin" | "organiser" | "regular";

interface ViewAsState {
  viewAs: ViewAs;
  setViewAs: (v: ViewAs) => void;
}

const ViewAsCtx = createContext<ViewAsState>({ viewAs: "admin", setViewAs: () => {} });

export function ViewAsProvider({ children }: { children: ReactNode }) {
  // In-memory only: a full page reload resets to "admin" (the safety property).
  const [viewAs, setViewAs] = useState<ViewAs>("admin");
  return <ViewAsCtx.Provider value={{ viewAs, setViewAs }}>{children}</ViewAsCtx.Provider>;
}

export function useViewAs() {
  return useContext(ViewAsCtx);
}

/**
 * Cap the role the UI renders as. Effective role per hunt = min(real role, ceiling).
 *   admin     -> unchanged
 *   organiser -> drop admin, keep organiser (can_manage) if real
 *   regular   -> drop both
 * Every gated site reads detail.hunt.can_manage / is_admin, so overriding those two
 * fields cascades to all of them with no per-component edits.
 */
export function applyViewAs(detail: HuntDetail, viewAs: ViewAs): HuntDetail {
  if (viewAs === "admin") return detail;
  const can_manage = viewAs === "organiser" ? detail.hunt.can_manage : false;
  return { ...detail, hunt: { ...detail.hunt, can_manage, is_admin: false } };
}
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0 (module compiles; `HuntDetail` import resolves).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/context/ViewAsContext.tsx
git commit -m "feat(dashboard): ViewAs context + applyViewAs ceiling helper"
```

### Task B2: Mount the provider in App

**Files:**
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Wrap AppShellLayout**

Add the import and wrap the authenticated render. Replace the `return (...)` block (lines 24–32):

```tsx
import { ViewAsProvider } from "./context/ViewAsContext";
```

```tsx
  return (
    <ViewAsProvider>
      <AppShellLayout user={me.data}>
        <Routes>
          <Route path="/" element={<HuntList />} />
          <Route path="/hunts/:uid" element={<HuntBoard />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AppShellLayout>
    </ViewAsProvider>
  );
```

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(dashboard): mount ViewAsProvider above the routes"
```

### Task B3: "View as" menu section + banner in AppShellLayout

**Files:**
- Modify: `frontend/src/components/AppShellLayout.tsx`

- [ ] **Step 1: Add imports**

Add to the existing `@mantine/core` import: `Alert`. Add to the `@tabler/icons-react`
import: `IconCheck, IconEye`. Add new imports:

```tsx
import { useAdminGuilds } from "../api";
import { useViewAs, type ViewAs } from "../context/ViewAsContext";
```

- [ ] **Step 2: Read view-as state and admin status in the component**

Inside `AppShellLayout`, after `const displayName = ...` (line 29):

```tsx
  const { viewAs, setViewAs } = useViewAs();
  const { data: adminGuilds } = useAdminGuilds();
  const isAdminSomewhere = (adminGuilds ?? []).length > 0;
  const VIEW_LABELS: Record<ViewAs, string> = {
    admin: "Admin (you)",
    organiser: "Organiser",
    regular: "Regular user",
  };
```

- [ ] **Step 3: Add the VIEW AS section to the menu dropdown**

In the `<Menu.Dropdown>` (currently just the Log out item), insert the view-as section
above the Log out item, gated on `isAdminSomewhere`:

```tsx
            <Menu.Dropdown>
              {isAdminSomewhere && (
                <>
                  <Menu.Label>View as</Menu.Label>
                  {(["admin", "organiser", "regular"] as ViewAs[]).map((level) => (
                    <Menu.Item
                      key={level}
                      leftSection={
                        viewAs === level ? <IconCheck size={16} /> : <span style={{ width: 16 }} />
                      }
                      onClick={() => setViewAs(level)}
                    >
                      {VIEW_LABELS[level]}
                    </Menu.Item>
                  ))}
                  <Menu.Divider />
                </>
              )}
              <Menu.Item leftSection={<IconLogout size={16} />} onClick={onLogout}>
                Log out
              </Menu.Item>
            </Menu.Dropdown>
```

- [ ] **Step 4: Add the banner under the header**

Replace `<AppShell.Main>{children}</AppShell.Main>` (line 68) with a banner + main:

```tsx
      <AppShell.Main>
        {viewAs !== "admin" && (
          <Alert
            color="orange"
            mb="md"
            icon={<IconEye size={16} />}
            withCloseButton={false}
          >
            <Group justify="space-between" wrap="nowrap">
              <Text size="sm">Viewing as: {VIEW_LABELS[viewAs]}</Text>
              <UnstyledButton onClick={() => setViewAs("admin")}>
                <Text size="sm" fw={600} c="orange">
                  Restore
                </Text>
              </UnstyledButton>
            </Group>
          </Alert>
        )}
        {children}
      </AppShell.Main>
```

- [ ] **Step 5: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/AppShellLayout.tsx
git commit -m "feat(dashboard): View-as menu section + downgraded banner"
```

### Task B4: Apply the ceiling in HuntBoard

**Files:**
- Modify: `frontend/src/pages/HuntBoard.tsx`

- [ ] **Step 1: Import and derive the adjusted detail**

Add import:

```tsx
import { useViewAs, applyViewAs } from "../context/ViewAsContext";
```

Inside the component, after `const { uid } = useParams();` add:

```tsx
  const { viewAs } = useViewAs();
```

After the loading/error guards (immediately after the `if (error || !data)` block, ~line 56) add:

```tsx
  const detail = applyViewAs(data, viewAs);
```

- [ ] **Step 2: Use `detail` for rendering/gating**

In the `return (...)` JSX block (lines ~62–164), replace every `data.` reference with
`detail.` — these are: `detail.hunt.url`, `detail.hunt.name`, `detail.hunt.archived`,
`detail.summary.*`, `detail.hunt.start_timestamp`, `detail.hunt.sheet_base_url`,
`detail.hunt.archive_sheet_base_url`, `detail.hunt.username`, `detail.hunt.password`,
`detail.hunt.can_manage`, `detail.hunt.is_admin`, `detail.board`, `detail.hunt.url_sep`,
`detail.hunt.puzzle_prefix`, and the `detail={detail}` props passed to `BoardGroup`,
`PuzzleTable`, `CreateControls`, `DragBoard`, `EvidencePanel`, `PuzzleDrawer`,
`AdminSettingsPanel`. Also update `pct` (line 60): `detail.summary`.

Leave the `allPuzzles` `useMemo` (lines 40–45) referencing `data` — the puzzle list is
unaffected by the ceiling, and hooks must run before the guards.

- [ ] **Step 3: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0. (If tsc reports a leftover `data.` in the JSX, convert it to `detail.`.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/HuntBoard.tsx
git commit -m "feat(dashboard): apply View-as ceiling to the hunt board"
```

### Task B5: HuntList "New hunt" respects the ceiling

**Files:**
- Modify: `frontend/src/pages/HuntList.tsx` (lines ~187–207)

- [ ] **Step 1: Gate isAdmin on the ceiling**

Add import:

```tsx
import { useViewAs } from "../context/ViewAsContext";
```

In the component that computes `isAdmin` (line 190), incorporate the ceiling:

```tsx
  const { viewAs } = useViewAs();
  const isAdmin = (adminGuilds ?? []).length > 0 && viewAs === "admin";
```

(The "New hunt" button at line 207 is gated on `isAdmin`, so it now hides when downgraded.)

- [ ] **Step 2: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/HuntList.tsx
git commit -m "feat(dashboard): hide New hunt when viewing as non-admin"
```

---

## Verification (end of plan)

- [ ] Backend: `timeout 180 pipenv run python -m pytest tests/ -q > /tmp/test.log 2>&1; echo exit=$?; tail -n 20 /tmp/test.log` — full suite green.
- [ ] Backend lint: `pipenv run ruff check` — clean.
- [ ] Frontend: `cd frontend && npx tsc --noEmit` — exit 0.
- [ ] Manual (if running the app): a custom-avatar account shows its picture; the avatar
  menu shows "View as" for an admin; selecting Organiser hides admin-only UI but keeps
  organiser controls; Regular hides both and hides "New hunt"; the banner appears while
  downgraded and a reload restores Admin.

## Rollback / recovery notes

- Each task is a standalone commit; revert any single commit to back out that step.
- Part A and Part B are independent — Part A can ship without Part B and vice versa.
- Part B is frontend-only; no migration or backend state to roll back.
