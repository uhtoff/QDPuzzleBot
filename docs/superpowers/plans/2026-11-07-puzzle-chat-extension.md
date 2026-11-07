# Puzzle Chat Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Chrome MV3 extension that shows a puzzle's Discord chat in Chrome's side panel beside the puzzle's sheet (read + reply), plus dashboard actions "Work on this puzzle", "Open round" (tab group) and "Close solved", and tab reuse for already-open puzzle sheets.

**Architecture:** Three additive server pieces — an extension-origin allow-list for the live websocket, a "which puzzle is this sheet?" lookup route, and a reply route that posts through a per-channel webhook after live permission checks. A new `extension/` directory holds the extension: pure, unit-tested helpers (sheet-URL parsing, tab→puzzle registry, message formatting) plus thin Chrome glue (service worker, side panel page, options page, a dashboard content script). The SPA shows the new buttons only when the content script announces the extension.

**Tech Stack:** Python 3.12 / Quart / discord.py (server), Chrome MV3 with plain ES modules and no bundler (extension), Vitest for extension unit tests, React + Mantine (dashboard buttons).

**Spec:** `docs/superpowers/specs/2026-11-07-puzzle-chat-extension-design.md`

## Global Constraints

- Additive only: no existing route, event, bang command or dashboard behaviour changes; no schema change.
- Every new state-changing route sits under `/api/` so the existing CSRF guard (`X-CSRF-Token` header vs `session["csrf_token"]`) applies.
- The websocket origin allow-list adds only origins listed in `config.json` → `web.extension_origins`; never wildcard.
- Reply route refuses unless: logged in, hunt visible (`hunt_visible`), hunt not archived, channel exists, live `view_channel` and `send_messages` for the member, member not timed out, content 1–2000 chars after strip, content not starting with `!`. Webhook sends use `allowed_mentions=discord.AllowedMentions.none()`.
- Files mode is the target for tab reuse / open round / close solved; in tabs mode only the panel works (follows the current `#gid=`).
- Python tests: `timeout 180 pipenv run python -m pytest <files> -q -p no:cacheprovider > <log> 2>&1`; `pipenv run ruff check`; `timeout 300 pipenv run basedpyright bot/` = 0 errors.
- Extension tests: `cd extension && timeout 120 npm test`.
- Work in worktree `.claude/worktrees/working-3`, branch `working-3`, **based on `main`, not `staging`** — the extension ships to live ahead of the 2026-11 cleanup now on `staging` (copy nothing — config is already there). Never merge `staging` into this branch.
- For test-instance checks, merge `working-3` into `staging` (a merge commit, never a rebase) and push `staging`; expect small conflicts only in `frontend/src/components/BoardGroup.tsx` and `docs/user-guide.md`. Shipping = fast-forward `main` to `working-3` — only when the user says so (live is held while hunts run; the user may choose to try it on a running hunt).

## Review Focus

1. A reply to a channel the user can't see or post in (role overwrite, timeout, archived hunt, deleted channel) must be refused with no webhook call — Task 3 tests.
2. A message starting with `!` (even after leading whitespace) is refused — Task 3 `test_reply_refuses_bang_command`.
3. The websocket still rejects every foreign origin when `extension_origins` is empty or missing — Task 1 `test_ws_origin_rejects_unlisted_extension`.
4. A sheet URL in tabs mode whose `#gid=` changes in the same tab re-targets the panel; a URL with no gid in files mode still matches — Task 4 `sheetUrl` tests + Task 2 lookup tests.
5. Tab reuse never closes the user's last tab in a window and never fires for a non-puzzle URL — Task 8 registry tests.

---

### Task 0: Checkpoint — extension cookie auth (user runs it)

The extension authenticates with the user's existing dashboard session cookie (`SameSite=Lax`, HttpOnly). Chrome sends such cookies on extension `fetch`es to hosts the extension has host permission for — this must be confirmed in the real browser before Tasks 5–8 rely on it.

- [x] **Step 1:** In the scratchpad, write a 3-file unpacked extension (manifest with `host_permissions: ["<test dashboard origin>/*"]`, a popup calling `fetch(origin + "/api/hunts", {credentials: "include"})` and printing status + body length).
- [x] **Step 2:** Ask the user to load it (chrome://extensions → Developer mode → Load unpacked) while logged into the test dashboard and report the status. 200 → proceed. 401 → ledger a Ruling switching Tasks 5–8 to a token handed over by the dashboard content script (new `GET /api/extension/token` returning a short-lived signed token, accepted by a `login_required`-equivalent) and re-plan those tasks before continuing.

### Task 1: Server — extension origins for the websocket

**Files:**
- Modify: `bot/utils/config.py` (web block, ~:43-66), `config.json.dist` (web block), `bot/web/api.py` `_ws_origin_allowed` (~:709)
- Test: `tests/test_web_hardening_lows.py`

**Interfaces:**
- Produces: `config.web_extension_origins: list[str]` (default `[]`).

`_ws_origin_allowed(origin)` returns True iff `_dashboard_origin()` is None (unchanged), or `origin == _dashboard_origin()`, or `origin in config.web_extension_origins`.

- [ ] **Step 1: Tests** (follow `_real_app()` style already in the file):
  - `test_ws_origin_accepts_listed_extension` — `extension_origins = ["chrome-extension://abc"]`, Origin `chrome-extension://abc` → handshake accepted.
  - `test_ws_origin_rejects_unlisted_extension` — same config, Origin `chrome-extension://evil` → closed 1008; and with `extension_origins` unset → `chrome-extension://abc` closed 1008.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement (config attr + `"extension_origins": []` in config.json.dist + the `in` check). **Step 4:** Run the file + `tests/test_csrf.py` → pass.
- [ ] **Step 5:** Commit `feat(web): allow configured extension origins on the activity websocket`.

### Task 2: Server — look up the puzzle behind a sheet URL

**Files:**
- Modify: `bot/web/data.py` (new function next to `get_hunt_puzzles`), `bot/web/api.py` (new route)
- Test: `tests/test_puzzle_lookup_web.py` (new)

**Interfaces:**
- Produces: `GET /api/puzzles/by-sheet?spreadsheet=<id>&gid=<int?>` (login_required) →
  `200 {"puzzle": {"id", "name", "solved", "channel_id": str|null, "discord_url", "sheet_url", "hunt_uid", "hunt_archived", "sheet_mode"}, "bridge_enabled": bool}`, `404 {"error": "not_found_or_forbidden"}` when no visible puzzle matches.
- Produces: `data.find_puzzle_by_sheet(spreadsheet_id: str, gid: int | None) -> dict | None` — the raw match (puzzle row fields + hunt row) before visibility.

Match rules: a top-level puzzle (`parent_puzzle_id IS NULL`) whose own `google_sheet_id == spreadsheet` (files mode; gid ignored), else a puzzle whose hunt's `google_sheet_id` or `solved_sheet_id == spreadsheet` and whose `google_page_id == gid` (tabs mode; gid required). Mini puzzles' tabs resolve to their parent minimeta (`parent_puzzle_id`). Visibility: `resolve_membership` + `hunt_visible` (same as `_channel_messages_visible`). `bridge_enabled` = `bool(config.web_bridge_enabled)`.

- [ ] **Step 1: Tests** (patch `bot.web.auth.session`, `bot.web.api.data.find_puzzle_by_sheet`, `resolve_membership`, `hunt_visible`, as `test_delete_mini_web.py` does): `test_lookup_returns_visible_puzzle_and_bridge_flag`, `test_lookup_hidden_hunt_is_404`, `test_lookup_unknown_sheet_is_404`, `test_lookup_requires_login` (401). Plus a live-DB test `tests/test_puzzle_lookup_live_db.py`: files-mode row matched by sheet id alone; tabs-mode row matched by hunt sheet + gid; tabs-mode wrong gid → None; a mini's tab → its parent.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** Run both files → pass.
- [ ] **Step 5:** Commit `feat(web): look up the puzzle behind a sheet URL`.

### Task 3: Server — reply to a puzzle channel via webhook

**Files:**
- Create: `bot/core/channel_relay.py`
- Modify: `bot/web/api.py` (new route)
- Test: `tests/test_channel_reply_web.py` (new), `tests/test_channel_relay.py` (new)

**Interfaces:**
- Produces: `async def relay_webhook(channel) -> discord.Webhook` — the channel's webhook named `"Puzzbot relay"`, created if absent (never reuses the archiver's).
- Produces: `async def send_as(channel, member, content: str) -> int` — sends via `relay_webhook` with `username=member.display_name`, `avatar_url=member.display_avatar.url`, `allowed_mentions=AllowedMentions.none()`, `wait=True`; returns the message id.
- Produces: `POST /api/channels/<int:channel_id>/messages` body `{"content": str}` → `201 {"id": str}`; errors: `400 {"error":"empty"|"too_long"|"command"}`, `403 {"error":"archived"|"forbidden"}`, `404 {"error":"not_found_or_forbidden"}`.

Order of checks (route): content validation → hunt by channel (`data.get_hunt_by_channel_id`) + `hunt_visible` (404) → archived (403) → guild/channel/member resolve (`bot_app().bot.get_guild`, `guild.get_channel`, `get_or_fetch_member`; any missing → 404) → `member.is_timed_out()` or not (`perms.view_channel and perms.send_messages`) → 403 `forbidden` → `send_as`.

- [ ] **Step 1: Tests** — route: `test_reply_posts_as_member` (send_as awaited with the member and stripped content), `test_reply_refuses_bang_command` (`"  !solve X"` → 400 command, no send), `test_reply_refuses_empty_and_too_long`, `test_reply_hidden_hunt_is_404`, `test_reply_archived_is_403`, `test_reply_without_send_permission_is_403`, `test_reply_timed_out_member_is_403`, `test_reply_missing_channel_is_404`. Relay: `test_relay_webhook_reuses_named_hook_and_ignores_archiver`, `test_send_as_disables_mentions_and_uses_display_name`.
- [ ] **Step 2:** Run → fail. **Step 3:** Implement. **Step 4:** Run → pass; mutation: drop the `send_messages` check → `test_reply_without_send_permission_is_403` fails; restore.
- [ ] **Step 5:** Commit `feat(web): reply to a puzzle channel through a relay webhook`.

### Task 4: Extension skeleton, pure helpers, CI

**Files:**
- Create: `extension/manifest.json`, `extension/package.json` (vitest only), `extension/src/lib/sheetUrl.js`, `extension/src/lib/registry.js`, `extension/src/lib/format.js`, `extension/test/*.test.js`, `extension/README.md`
- Modify: `.github/workflows/ci.yml` (an `extension` job mirroring `frontend`: `working-directory: extension`, `npm ci`, `npm test`)

**Interfaces:**
- `parseSheetUrl(url: string) -> {spreadsheetId: string, gid: number|null} | null` — Google Sheets `…/spreadsheets/d/<id>/…` with gid from `#gid=` or `?gid=`; anything else → null.
- `createRegistry()` → `{set(tabId, puzzle), get(tabId), delete(tabId), findBySpreadsheet(id) -> tabId|null, tabsForChannel(channelId) -> tabId[], solvedTabs() -> tabId[], update(puzzleId, patch)}` — the tab→puzzle memory (the spec's "remember the match").
- `messageView(msg) -> {author, avatar, text, time}` and `isBangCommand(text) -> boolean` (leading whitespace ignored).

Manifest: MV3; `side_panel.default_path: "src/panel.html"`; `background.service_worker: "src/background.js"`, `type: "module"`; permissions `sidePanel, tabs, tabGroups, storage, cookies, scripting, notifications`; `host_permissions: ["https://docs.google.com/*"]`; `optional_host_permissions: ["https://*/*"]` (the dashboard origin, requested from the options page); `options_page: "src/options.html"`; `minimum_chrome_version: "116"`.

- [ ] **Step 1: Tests** — `sheetUrl.test.js`: files URL without gid, tabs URL with `#gid=123`, `?gid=` form, non-Sheets URL → null, Sheets "Solved" spreadsheet URL. `registry.test.js`: set/get/delete; `findBySpreadsheet` ignores tabs whose puzzle came from a different spreadsheet; `solvedTabs` after `update(id, {solved: true})`; `tabsForChannel`. `format.test.js`: `isBangCommand("  !s X") === true`, `"hi !s"` false.
- [ ] **Step 2:** `npm install` (creates lock), run → fail. **Step 3:** Implement. **Step 4:** Pass.
- [ ] **Step 5:** Commit `feat(extension): skeleton, sheet-URL parsing and tab registry`.

### Task 5: Extension — side panel follows the puzzle (read-only chat, live)

**Files:** Create `extension/src/background.js`, `extension/src/api.js`, `extension/src/panel.html`, `extension/src/panel.js`, `extension/src/panel.css`, `extension/src/options.html`, `extension/src/options.js`.

Behaviour:
- Options page stores `dashboardOrigin` (default live origin from README) in `chrome.storage.sync` and requests host permission for it.
- `api.js`: `apiGet(path)` / `apiSend(path, method, body)` against `dashboardOrigin` with `credentials: "include"`; the CSRF header value comes from `chrome.cookies.get({url: dashboardOrigin, name: "csrf_token"})`. 401 → `{authRequired: true}`.
- Background: on `tabs.onUpdated` (url change, incl. hash) and `tabs.onActivated`, `parseSheetUrl` → `GET /api/puzzles/by-sheet` → `registry.set`; `chrome.sidePanel.setOptions({tabId, path: "src/panel.html", enabled: true})` for puzzle tabs, `enabled: false` otherwise; `chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true})`. One websocket to `wss://<origin>/api/ws/activity`, reconnect with backoff; relays `message` / `message_edit` / `message_delete` events to panel ports via `chrome.runtime` messaging.
- Panel: on load and on `chrome.tabs.onActivated`, ask the background for the active tab's puzzle; render the last 50 messages (`GET /api/channels/<id>/messages`), append live events for that channel; states "not a puzzle", "log in to the dashboard" (link), "chat unavailable — open in Discord" (`bridge_enabled` false), "no Discord channel".

- [ ] **Step 1:** Implement (glue; logic stays in the Task 4 helpers).
- [ ] **Step 2:** Manual check with the user on the test instance (load unpacked, set test origin, add the extension origin to the test `config.json` `web.extension_origins`): open a files-mode puzzle sheet → panel shows its chat; post in Discord → appears within a few seconds; switch to another puzzle tab with the panel pinned → panel follows; a tabs-mode sheet, switch gid → panel follows.
- [ ] **Step 3:** Commit `feat(extension): side panel shows the active puzzle's chat`.

### Task 6: Extension — reply from the panel

**Files:** Modify `extension/src/panel.js`, `panel.html`, `panel.css`.

- Composer (textarea; Enter sends, Shift+Enter newline); `isBangCommand` → inline hint "Run commands in Discord" without sending; `POST /api/channels/<id>/messages`; map error codes to short inline messages; the sent message arrives back through the live feed (no optimistic duplicate).
- [ ] **Step 1:** Extend `format.test.js` with `errorText(code)` for `empty|too_long|command|archived|forbidden|not_found_or_forbidden` → fail → implement → pass.
- [ ] **Step 2:** Implement composer. **Step 3:** Manual check: reply appears in Discord under the user's name/avatar with "APP"; `!s X` is refused in the panel.
- [ ] **Step 4:** Commit `feat(extension): reply from the side panel`.

### Task 7: Extension — unread badge and optional notifications

**Files:** Modify `extension/src/background.js`, `options.html/js`; Create `extension/src/lib/unread.js` + test.

- `createUnread()` → `{onMessage(channelId, panelVisibleChannelId), clear(channelId), total()}`; counts only channels with an open puzzle tab, not the channel currently visible in an open panel. Badge text = total (empty at 0). Notifications (off by default, options toggle): one `chrome.notifications` per message with puzzle name; click → focus that tab (the user then opens the panel; Chrome forbids opening it for them).
- [ ] **Step 1:** `unread.test.js` (counts, clear, visible-channel exclusion, untracked channel ignored) → fail → implement → pass. **Step 2:** Glue + manual check. **Step 3:** Commit `feat(extension): unread badge and optional notifications`.

### Task 8: Dashboard actions, tab reuse, close solved

**Files:**
- Create: `extension/src/dashboard.js` (content script, registered with `chrome.scripting.registerContentScripts` for `dashboardOrigin/*`)
- Modify: `extension/src/background.js`, `extension/src/lib/registry.js` (+test)
- Modify: `frontend/src/components/PuzzleRowActions.tsx` (:22), `frontend/src/components/PuzzleDrawer.tsx` (body Stack ~:177), `frontend/src/components/BoardGroup.tsx` (header icons ~:172-222), `frontend/src/pages/HuntBoard.tsx` (a "Close solved tabs" control), new `frontend/src/lib/extension.ts` (+test)

**Interfaces:**
- Content script sets `document.documentElement.dataset.puzzbotExtension = "1"` and relays `window.postMessage({source: "puzzbot-dashboard", action, payload})` to the background; only `event.source === window` and the dashboard's own origin are accepted.
- `frontend/src/lib/extension.ts`: `extensionPresent(): boolean`; `workOn(puzzle: {id, sheet_url, hunt_uid})`, `openRound(name: string, sheetUrls: string[])`, `closeSolved()` — each a `window.postMessage`. Buttons render only when `extensionPresent()`.
- Background actions:
  - `work` → `registry.findBySpreadsheet` → focus existing tab, else `tabs.create`; files mode only reuses (tabs mode just opens the URL).
  - `openRound` → create tabs for URLs not already open, `tabs.group`, `tabGroups.update({title: name})`; already-open tabs are moved into the group.
  - `closeSolved` → re-lookup every registered tab, close those whose puzzle is solved.
  - Tab reuse: on `tabs.onUpdated` for a new files-mode puzzle URL already open in another tab → focus the existing tab and close the new one, unless it is the only tab in its window.
- [ ] **Step 1: Tests** — `registry.test.js`: `reuseTarget(newTabId, spreadsheetId, windowTabCount)` returns the existing tab id or null (null for the only tab in a window, for tabs-mode puzzles, for the same tab). `extension.test.ts`: buttons hidden when the attribute is absent; `workOn` posts the right message shape.
- [ ] **Step 2:** Fail → implement → pass; `cd frontend && npm test && npm run build`.
- [ ] **Step 3:** Manual check: Work on this (opens / switches), clicking the sheet link in the Discord pin switches to the open tab, Open round makes a named group, Close solved closes only solved puzzles' tabs.
- [ ] **Step 4:** Commit `feat: dashboard actions for the extension, tab reuse, close solved`.

### Task 9: Docs and packaging

**Files:** `extension/README.md` (install unpacked, options, the `web.extension_origins` server entry, Web Store packaging notes), `docs/user-guide.md` (new "Chrome extension" section), `docs/dashboard.md` (`extension_origins` config key), `README.md` Documentation table row.

- [ ] **Step 1:** Write. **Step 2:** Commit `docs: puzzle chat extension`.
