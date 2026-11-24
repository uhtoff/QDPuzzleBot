# Current Spec — channel-name clashes get a -N suffix (with Accept/Cancel)

## Goal

A puzzle must always get its OWN Discord channel. When creating or renaming a puzzle
would give its channel the same name as an existing channel in the same category,
ask the user: "This would lead to two channels named #x; the new one will be named
#x-1 to avoid confusion" — Accept / Cancel. Accept → channel gets the next free
`-N` suffix. Reconcile must never relink a channel that is already in use.

Trigger: the hunt's own `#general` could become a puzzle channel — create a puzzle
"General" in the hunt category (create_puzzle finds the existing channel, created=False,
leaves the puzzle UNLINKED), then `!reconcile apply` step 2 relinks it to #general.
Same mechanism for any two names that clean to the same channel name ("Foo Bar" /
"Foo-Bar" pass the exact-name duplicate check), a metaless group's chat channel, or a
hand-made channel.

## In scope

1. Reconcile relink (`PuzzleService._reconcile_channel` step 2): never adopt a channel
   that is some puzzle's channel_id, the hunt's channel_id, or a group chat channel.
   Report it instead (leave unlinked).
2. Suffix preservation: `desired_channel_name` keeps a trailing `-N` already present on
   the puzzle's stored `channel_name` when stored == optional glyph prefix + clean + `-N`.
   (Every glyph refresh / reconcile rename / backfill goes through it.)
3. Create: `create_puzzle(..., on_channel_clash=...)`:
   - `"ask"` (interactive callers): raise `ChannelNameClash(existing, suggested)` BEFORE
     any sheet/DB work when the target category already has a text channel of that name.
   - `"suffix"`: use the next free `-N` name (Accept, and non-interactive callers).
   - `"adopt"`: today's behaviour, for `_promote_group_to_target` only.
   - At the authoritative create point, if the name is now taken (race), suffix.
   - Default: `"suffix"` (safe: never unlinked, never shared).
4. Rename: `rename_puzzle(..., on_channel_clash="ask"|"suffix")` — clash = another
   channel (not the puzzle's own) of that name in the puzzle channel's category.
5. Discord: `!puzzle` and `!rename_puzzle` show ConfirmView Accept/Cancel on clash.
6. Web: `POST /hunts/<uid>/puzzles`, the canvas region create route, and
   `POST /puzzles/<id>/rename` return 409 `{"error":"channel_name_clash",
   "channel": "<existing>", "suggested": "<x-1>"}` unless body has
   `"accept_channel_suffix": true`. Dashboard (CreateControls, RegionModal,
   PuzzleDrawer rename) shows Accept/Cancel and resubmits with the flag.
7. Docs: commands.md / user-guide where create/rename are described.

## Out of scope

- Group renames, hunt renames, voice channel names.
- Retro-fixing existing unlinked puzzles or existing duplicate channels.

## Relevant files

- bot/core/puzzle_service.py — create_puzzle (~3256), rename_puzzle (~2892),
  desired_channel_name (~402), _reconcile_channel (~478), _promote_group_to_target (~1535)
- bot/core/discord_channels.py — get_or_create_channel, clean_channel_name
- bot/core/target_glyph.py — GLYPHS, PRIMARY_MARKER
- bot/cogs/puzzles.py — !puzzle (~1039), !rename_puzzle (~3735), ConfirmView use
- bot/web/actions.py — create (~2730), region create (~3655), rename (~755)
- frontend/src/api.ts, components/CreateControls.tsx, RegionModal.tsx, PuzzleDrawer.tsx

## Constraints

- Branch `channel-name-clash` in worktree `.claude/worktrees/channel-name-clash`
  (based on local staging 6faee3c, which has unpushed commits).
- Run tests via `timeout … pipenv run python -m pytest …`; basedpyright bot/ before push.
- Puzzle name itself never changes; only the channel gets `-N`.

## Acceptance criteria

- A puzzle named "General" in the hunt category: interactive → prompt; accept → #general-1.
- `!reconcile apply` never links a puzzle to #general / another puzzle's channel / group chat.
- Glyph refresh and reconcile keep `-N`.
- Full pytest + basedpyright + ruff + frontend typecheck/tests green.

## Phases

1. Core: suffix helpers + desired_channel_name preservation + reconcile relink guard.
2. Core: create_puzzle / rename_puzzle `on_channel_clash` + ChannelNameClash.
3. Discord prompts (!puzzle, !rename_puzzle).
4. Web routes + dashboard dialogs.
5. Docs.
