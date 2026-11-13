# Puzzle Chat Firefox Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Puzzle Chat extension installable and working in desktop Firefox 140+ from the same source tree, with the Chrome build unchanged.

**Architecture:** The Chrome `manifest.json` stays the single source; a pure transform derives the Firefox manifest at package time. Side panel vs sidebar differences live in one injected-API module (`src/lib/panelHost.js`). The server accepts well-formed `moz-extension://` origins alongside the exact allowlist.

**Tech Stack:** MV3 WebExtension (plain ES modules, vitest), Python/Quart (pytest), bash `package.sh`.

**Spec:** `docs/superpowers/specs/2026-11-13-firefox-extension-port-design.md`

## Global Constraints

- Branch `firefox-port`, based on `staging` @ 331e117. Do not merge to `main`/`staging` without the user.
- `extension/manifest.json` must not change (Chrome Store build stays byte-identical in content).
- Firefox floor: `"strict_min_version": "140.0"`. Gecko id: `"puzzle-chat@puzzbot.com"`.
- Origin pattern, exact: `^moz-extension://[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`.
- `data_collection_permissions`: `{ "required": ["authenticationInfo", "websiteContent", "personalCommunications"] }`.
- Worktree bootstrap before any pytest: copy `config.json` and `google_secrets.json` from `/home/russ/projects/puzzbot/` into the worktree root (gitignored; conftest dies without them). Run `npm ci` in `extension/` if `node_modules` is absent.
- Test commands (single statement, wrapped):
  - JS: `timeout 120 npm --prefix extension test > /tmp/ext-test.log 2>&1`
  - Python: `timeout 180 pipenv run python -m pytest tests/test_ws_ticket.py -q > /tmp/test.log 2>&1`
  - Types: `pipenv run basedpyright bot/` → 0 errors.
- Comments and docs use the existing plain style; UI copy says "Puzzle Chat".

## Review Focus

- **Toolbar listener registered twice** (`setupPanel()` runs at top level *and* in `onInstalled`): in Firefox two `toggle()` calls per click cancel out, so the button looks dead. `setup()` must be idempotent — pinned in Task 2.
- **Malformed or look-alike `moz-extension` origins** (`moz-extension://abc`, a trailing path, uppercase hex, `moz-extension://<uuid>.evil.test`): must be refused — pinned in Task 1.
- **Firefox sidebar closed when "Work on this puzzle" / a notification is clicked:** tab switch must still happen and nothing may throw — `open()` is a safe no-op, pinned in Task 2.
- **Sheets host permission revoked in `about:addons`:** every tab silently reads "not a puzzle" — options-page button + onInstalled prompt in Task 4 (inspection-only + manual smoke; no DOM test runner exists).
- **Chrome regression:** the Chrome path must still call `setPanelBehavior({openPanelOnActionClick: true})`, `setOptions` and `sidePanel.open` — pinned in Task 2; `package.sh` with no argument keeps its old output — checked in Task 3.

Not testable here, carried to the manual smoke list (Task 5): Firefox suspending the background event page while the websocket is idle (reconnect alarm should recover), and Firefox sending dashboard cookies on the extension's `credentials: "include"` fetches.

---

### Task 1: Server accepts Firefox extension origins

**Files:**
- Modify: `bot/web/api.py:806-821` (`_ws_origin_allowed`) and `:855-860` (ticket path in `ws_activity`)
- Test: `tests/test_ws_ticket.py`

**Interfaces:**
- Produces: `_is_extension_origin(origin: str | None) -> bool` in `bot/web/api.py`.

- [ ] **Step 1: Write the failing tests** in `tests/test_ws_ticket.py`, reusing `_open` (which sets `web_extension_origins = [EXT]`, a Chrome origin, so the Firefox origin is *not* listed):

```python
MOZ = "moz-extension://0123abcd-4567-89ef-0123-456789abcdef"

async def test_ws_firefox_origin_with_ticket_proceeds_without_listing():
    from bot.web import ws_ticket
    assert await _open(MOZ, ws_ticket.issue(77)) == 77

async def test_ws_firefox_origin_without_ticket_is_closed():
    assert await _open(MOZ, None) is None

@pytest.mark.parametrize("origin", [
    "moz-extension://abc",
    MOZ + "/x",
    MOZ.upper().replace("MOZ-EXTENSION", "moz-extension"),
    MOZ + ".evil.test",
    "https://" + MOZ[len("moz-extension://"):],
])
async def test_ws_malformed_firefox_origin_is_closed(origin):
    from bot.web import ws_ticket
    assert await _open(origin, ws_ticket.issue(3)) is None

def test_is_extension_origin_unit():
    from bot.web import api
    with patch("bot.web.api.config") as cfg:
        cfg.web_extension_origins = [EXT]
        assert api._is_extension_origin(EXT)
        assert api._is_extension_origin(MOZ)
        assert not api._is_extension_origin(None)
        assert not api._is_extension_origin("chrome-extension://other")
```

(Mark async tests `@pytest.mark.asyncio` to match the file's style.)

- [ ] **Step 2: Run** the Python test command. Expected: the new Firefox-proceeds and unit tests FAIL (`_is_extension_origin` missing / closed socket); the malformed cases may already pass.

- [ ] **Step 3: Implement** `_is_extension_origin` with a module-level compiled `re.fullmatch` of the Global Constraints pattern, OR exact membership in `config.web_extension_origins` (keep the `isinstance(list)` guard). `_ws_origin_allowed` returns `origin == expected or _is_extension_origin(origin)`; the ticket branch in `ws_activity` uses `_is_extension_origin(websocket.headers.get("Origin"))`. Update both docstrings/comments to say Firefox origins are per-install UUIDs and why accepting the scheme is safe (web pages cannot send it).

- [ ] **Step 4: Run** the Python test command and `pipenv run basedpyright bot/`. Expected: all pass, 0 errors. Mutation check: change `fullmatch` to `match` and confirm the `MOZ + "/x"` case goes red; restore.

- [ ] **Step 5: Commit** — `feat(web): accept Firefox moz-extension origins on the activity websocket`

### Task 2: panelHost — side panel vs sidebar

**Files:**
- Create: `extension/src/lib/panelHost.js`
- Modify: `extension/src/background.js:141`, `:152-156` (`setupPanel`), `:586`
- Test: `extension/test/panelHost.test.js`

**Interfaces:**
- Produces: `createPanelHost({ sidePanel, sidebarAction, action, path }) -> { setup(): void, open(windowId: number): void }`
  - `sidePanel` present (Chrome): `setup` calls `sidePanel.setPanelBehavior({ openPanelOnActionClick: true })` and `sidePanel.setOptions({ path, enabled: true })` (both `.catch`ed); `open(w)` calls `sidePanel.open({ windowId: w })` synchronously and swallows rejection.
  - else `sidebarAction` present (Firefox): `setup` adds **one** `action.onClicked` listener (across any number of `setup()` calls) whose body calls `sidebarAction.toggle()` synchronously, no `await` before it; `open` does nothing.
  - neither: both no-ops.
  - Neither method returns a promise or throws, including when the underlying call throws synchronously.

- [ ] **Step 1: Write the failing tests** with hand-rolled fakes (`vi.fn()` returning `Promise.resolve()`):
  - `chrome: setup sets behaviour and path` — asserts `setPanelBehavior` called with `{ openPanelOnActionClick: true }`, `setOptions` with `{ path: "src/panel.html", enabled: true }`, and `action.onClicked.addListener` not called.
  - `chrome: open targets the window` — `open(7)` → `sidePanel.open` called with `{ windowId: 7 }`; a rejecting `open` produces no unhandled rejection (await a tick).
  - `firefox: setup twice registers one listener` — call `setup()` twice; `addListener` called once; invoking the captured listener calls `sidebarAction.toggle` exactly once.
  - `firefox: open is a no-op and never throws` — `open(3)` with `sidebarAction.open` as a `vi.fn()` → not called; no throw.
  - `firefox: listener toggles synchronously` — the listener returns non-promise and `toggle` was called before the listener returned.
  - `neither: no-ops` — `createPanelHost({ path })` then `setup(); open(1)` do not throw.

- [ ] **Step 2: Run** the JS test command. Expected: FAIL (module not found).

- [ ] **Step 3: Implement** `panelHost.js`, then in `background.js` build one host at top level:
  `const panelHost = createPanelHost({ sidePanel: chrome.sidePanel, sidebarAction: globalThis.browser?.sidebarAction ?? chrome.sidebarAction, action: chrome.action, path: PANEL_PATH });`
  `setupPanel()` becomes `panelHost.setup()`; lines 141 and 586 become `panelHost.open(...)` with the same window arguments (keep line 141 before any `await`). Update the "One global side panel" comment to mention the Firefox sidebar and that Firefox cannot open it from these handlers.

- [ ] **Step 4: Run** the JS test command. Expected: all pass. `grep -n sidePanel extension/src/background.js` → only the `createPanelHost` argument. Mutation: drop the idempotence guard; the "setup twice" test goes red; restore.

- [ ] **Step 5: Commit** — `feat(extension): panelHost seam for Chrome side panel / Firefox sidebar`

### Task 3: Firefox manifest + package target

**Files:**
- Create: `extension/build/firefoxManifest.js` (pure transform), `extension/build/write-firefox-manifest.js` (CLI: reads `manifest.json`, writes JSON to the path in `argv[2]`, 2-space indent + trailing newline)
- Modify: `extension/package.sh`, `extension/.gitignore` (ensure `dist/` ignored — check first)
- Test: `extension/test/firefoxManifest.test.js`

**Interfaces:**
- Produces: `firefoxManifest(chromeManifest: object) -> object` — returns a new object, never mutates its input; field changes exactly as the spec's "Firefox manifest (derived)" list. `sidebar_action.default_icon` is a copy of `chromeManifest.action.default_icon`; `default_title` is `chromeManifest.name`.

- [ ] **Step 1: Write the failing tests**, importing the real `../manifest.json` (vitest `import m from "../manifest.json"` or `JSON.parse(readFileSync(...))`):
  - output lacks `side_panel`, `minimum_chrome_version`, `options_page`; `permissions` equals the input's minus `"sidePanel"`, other entries in original order.
  - `background` deep-equals `{ scripts: ["src/background.js"], type: "module" }`.
  - `sidebar_action` deep-equals `{ default_panel: "src/panel.html", default_title: "Puzzle Chat", default_icon: m.action.default_icon, open_at_install: false }`.
  - `options_ui` deep-equals `{ page: "src/options.html", open_in_tab: true }`.
  - `browser_specific_settings.gecko` deep-equals the Global Constraints values.
  - `host_permissions`, `optional_host_permissions`, `optional_permissions`, `version`, `action`, `icons` pass through unchanged.
  - input not mutated (`structuredClone` before, `toEqual` after).

- [ ] **Step 2: Run** the JS test command. Expected: FAIL.

- [ ] **Step 3: Implement** the transform and CLI. `package.sh`: take optional `$1` (`""` or `firefox`; anything else → usage error, exit 2). Chrome path unchanged. Firefox path: build the same `INCLUDE` list, copy it into a fresh `dist/firefox/` (rm -rf first), overwrite `dist/firefox/manifest.json` via `node build/write-firefox-manifest.js dist/firefox/manifest.json`, copy `../LICENSE` in, then zip the directory's contents to `dist/puzzle-chat-firefox-${version}.zip`; print both paths.

- [ ] **Step 4: Verify.** JS tests pass. Before editing `package.sh`, run it once and save `unzip -Z1 <chrome zip> | sort > /tmp/chrome-before.txt`; after, run it again and `diff` against a fresh listing. Expected: identical (the new `build/` dir must not leak into the Chrome zip — it is not under `src/`). Then `bash extension/package.sh firefox`; `python3 -m json.tool extension/dist/firefox/manifest.json` succeeds; if `npx --no-install web-ext` exists run `web-ext lint -s extension/dist/firefox` and record the result (warnings OK, errors not); if absent, say so.

- [ ] **Step 5: Commit** — `feat(extension): derive the Firefox manifest and package a Firefox build`

### Task 4: Prompt when Google Sheets access is missing

**Files:**
- Modify: `extension/src/options.html`, `extension/src/options.js`, `extension/src/background.js:183-190` (`onInstalled`)

**Interfaces:**
- Consumes: nothing new. Uses `const SHEETS = ["https://docs.google.com/spreadsheets/*"]` (define once in `src/lib/origin.js` as an export `SHEETS_ORIGINS` and import it in both files).

- [ ] **Step 1: Implement.** `options.html`: after the notify paragraph add `<p id="sheets" hidden>Puzzle Chat cannot see your Google Sheets tabs. <button id="sheets-grant">Allow access to Google Sheets</button></p>`. `options.js`: on load, `chrome.permissions.contains({ origins: SHEETS_ORIGINS })` → `sheets.hidden = ok`; button click calls `chrome.permissions.request({ origins: SHEETS_ORIGINS })` directly in the handler (no prior `await` — Firefox needs the user action), then re-hides on success and sets `#status` to "Google Sheets access allowed." / "Google Sheets access was not allowed.". Also re-check on `chrome.permissions.onAdded`/`onRemoved`. `background.js onInstalled` (install reason only): open options if the dashboard origin **or** the Sheets origins are not granted.

- [ ] **Step 2: Verify.** JS test command still passes (no DOM runner for options — inspection-only; state so in the report). `grep -c SHEETS_ORIGINS extension/src/*.js extension/src/lib/origin.js` shows the export and both uses.

- [ ] **Step 3: Commit** — `feat(extension): ask for Google Sheets access when it is missing`

### Task 5: Docs and smoke checklist

**Files:**
- Modify: `extension/README.md` (new "Firefox" section after "Install (unpacked)"), `docs/user-guide.md` ("Chrome extension (optional)" section, line ~337)
- Create: `extension/store/amo-notes.md`

- [ ] **Step 1: Write.**
  - README "Firefox": Firefox 140+; dev install = `bash package.sh firefox` → `about:debugging#/runtime/this-firefox` → Load Temporary Add-on → `dist/firefox/manifest.json`; release = upload `dist/puzzle-chat-firefox-<v>.zip` to AMO as **unlisted**, distribute the signed `.xpi`; server needs **no** `extension_origins` entry for Firefox (any `moz-extension://<uuid>` is accepted, and why); differences: toolbar button toggles the sidebar, Work/notification clicks cannot open it; `manifest.json` is Chrome's — the Firefox one is generated, never edit `dist/`. Also amend the `web.extension_origins` section to say it is for Chrome only.
  - User guide: retitle to "Browser extension (optional)" only if the heading is not linked elsewhere (`grep -rn "chrome-extension-optional\|Chrome extension" docs frontend/src` first; if linked, keep the title and add a Firefox paragraph). Firefox paragraph: how to install from the team link, open the sidebar with the toolbar button, Work switches tabs but you open the sidebar yourself.
  - `store/amo-notes.md`: the data-collection declaration (three categories, why each — copy reasoning from `listing.md` "Data use disclosure"), reviewer notes pointer to `reviewer-notes.md`/`reviewer-setup-playbook.md`, and the **manual smoke list**: toolbar toggles sidebar; sidebar follows active sheet tab; live message arrives; reply posts; Work / Open round as tab group / Open puzzle / Close solved; options origin save prompts; revoke Sheets access in about:addons → options button restores it; leave idle 5+ min then confirm messages still arrive (event-page suspension); dashboard cookie works for fetches (panel loads history).

- [ ] **Step 2: Verify.** Re-read the README section against the code (paths, commands exist). Run the JS test command once more.

- [ ] **Step 3: Commit** — `docs(extension): Firefox install, release and smoke notes`

## Verification (end of branch)

- JS test command, Python test command, `pipenv run basedpyright bot/`, `pipenv run ruff check bot tests` all clean.
- `git diff staging -- extension/manifest.json` is empty.
- Hand the user the manual smoke list from `extension/store/amo-notes.md`; the branch is not done until they report it.
