# Session Summary

Tool: Claude Code
Worktree: /home/russ/projects/puzzbot
Started: 2026-11-07
Status: ready for handoff — fix(web): browsers revalidate the dashboard's unhashed pages and data @ f52a9a9

<!-- worktree-census: maintained by stamp-handoff-status.sh, do not hand-edit -->
## Parallel worktrees — auto-maintained, read before starting work

Another session may be mid-task in these. A worktree ahead of `main` holds work that exists
nowhere else: its own `.ai/active/` is gitignored, and nothing is shared until it wraps into
`.ai/archive/`. Check one before rebuilding anything it may already have done, and harvest it
before deleting it — including its untracked `.ai/active/` notes.

- `compact-header` (board-tidy, 0 ahead of main) — last commit 2026-11-12 22:12: feat(manage): Manage view becomes one column of matching sections; danger zone last
- `matrix-spike` (3 ahead of main) — last commit 2026-07-31 17:59: docs(spike): park the matrix spike with an honest resume note
- `pdf-transcribe` (195 ahead of main) — last commit 2026-09-05 09:10: docs: round eighteen -- prose first, padded clues
- `voice` (0 ahead of main) — last commit 2026-11-08 13:08: fix(voice): voice cleanup turns per-puzzle voice off; !update_settings refuses every field the generic commit skips
- `working-3` (0 ahead of main) — last commit 2026-11-08 10:14: feat: remind at hunt creation when the server gates the organiser tier
<!-- /worktree-census -->

## Goal
Puzzle chat Chrome extension (MV3): a puzzle's Discord chat in Chrome's side panel
beside its sheet (read + reply via a per-channel webhook), "Work on this puzzle" from the
dashboard, tab reuse, "Open round" as a tab group, "Close solved". Files mode first;
tabs mode gets the panel only. Additive only — nothing existing may change behaviour.

Spec (authoritative): docs/superpowers/specs/2026-11-07-puzzle-chat-extension-design.md

## Current status
- Design approved by user 2026-11-07, committed 7c8ba9b on local staging (not pushed).
- Plan written (18f6999). Work MOVED to worktree working-3, rebased on main (38554f1) so the extension ships ahead of staging — see that worktree's .ai/active/session-summary.md.

## Final decisions (see spec "Decided" + designer's calls)
- Side panel for chat (Chrome can't auto-open it: unread badge instead).
- No split view / no 75-25 (Chrome Split View API can't size splits).
- Reply route: hunt-visible + live view_channel & send_messages, not timed out;
  refuse archived/missing channels; allowed_mentions none; refuse text starting "!".
- Extension remembers tab->puzzle mapping (tabs-mode solve moves the sheet).
- /api/ws/activity origin allow-list gets an explicit configured extension origin.
- Bridge is a GLOBAL setting (web.bridge_enabled), not per hunt.

## Rejected approaches
- Full web Discord in a split view (can't pop open, can't size).
- Running bang commands from the panel (would touch the command layer).

## Context from earlier in the same session (already shipped to staging, NOT live)
- main held behind staging until the currently running hunts finish (memory:
  live-deploy-held-2026-11). Then: main <- staging, push; then merge branch
  drop-dead-columns (migration 058).

## Next steps
1. Plan: docs/superpowers/plans/2026-11-07-puzzle-chat-extension.md (committed on local
   staging, not pushed). User reviewed the plan summary; execution method recommended:
   subagent-driven (superpowers:subagent-driven-development). Confirm with the user, then
   start at Task 0 (the user loads a tiny unpacked extension to prove cookie auth from an
   extension works; 401 => switch Tasks 5-8 to a dashboard-issued token, re-plan first).
2. Build in worktree .claude/worktrees/working-3 (fast-forward it to staging first; it
   already has config.json + google_secrets.json).
3. Manual Chrome checks in Tasks 0 and 5-8 are done by the user on the TEST instance
   (needs the extension's chrome-extension:// origin added to the test config.json
   web.extension_origins after Task 1 is deployed to staging).
4. Do NOT push to main: live is held until running hunts finish (memory
   live-deploy-held-2026-11).

## Open questions
None blocking.
