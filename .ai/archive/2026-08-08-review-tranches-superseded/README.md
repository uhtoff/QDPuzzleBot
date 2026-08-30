# Archived: working-2's 2026-08-07 review tranches — SUPERSEDED, not merged

This is the untracked `.ai/active/implementation-plan.md` harvested from the
`.claude/worktrees/working-2` worktree before it was deleted (2026-08-30). It existed
nowhere else: `.ai/active/` is gitignored, so an unwrapped worktree's notes are lost with
the worktree.

**Status: superseded.** Every tranche in this plan was independently implemented on
`staging` by different commits, so none of these 8 commits were merged and none are
missing:

| This plan's tranche | Landed on staging as |
|---|---|
| A — ops (O2/O3/O5) | `e758232`, `5121a09`, `478c841` |
| B — dead code | `d7f8086` |
| C — DB (T4/T5) | `417b97e`, `cc0d91f` |
| D — web/frontend hardening | `31e8f70`, `f7ddf29` |
| status vocabulary | `0187bdb` |

Verified before deletion: every dead symbol this plan targeted (`HuntSettings`,
`LADDER_SPOT_DATA_DIR`, `docs_url`, `drive_folder_url`, `extract_id_from_url`,
`Config.store`, `owner_email`, `puzzle_addons_path`) is absent from `staging` too, and the
net tree diff had the branch **behind** staging (1,283 insertions vs 5,138 deletions).

## Why it is kept anyway — one decision that still governs live code

The 2026-08-07 review listed **`DB_DSN`** among the dead config keys to remove. This plan
deliberately **kept** it, and was right to: it is a live env override for
`self.database` (`bot/utils/config.py:30`, "for docker") and removing it would silently
change behaviour for any deployment that sets it. That reasoning is recorded nowhere
else — preserved here so a future dead-config pass does not delete it.

The only cleanup in this branch that never reached staging: removing the unread
`puzzle_cell_link` key from `config.json.dist`. That is already logged as a low finding
in the 2026-08-30 review, so nothing is lost.
