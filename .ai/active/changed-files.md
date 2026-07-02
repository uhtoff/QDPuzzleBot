# Changed Files

## Git status

Branch: `working-1` (== `staging`, tip `bed73c9`). Working tree clean apart from the new
`.ai/active/` handoff files below. No source code changed yet.

## Files changed

- `.ai/active/current-spec.md` — new (spec for the 9 HIGH fixes)
- `.ai/active/implementation-plan.md` — new (finding tracker + phasing + protocol)
- `.ai/active/open-questions.md` — new (design questions, blocking + non-blocking)
- `.ai/active/session-summary.md` — new (status header)
- `.ai/active/changed-files.md` — new (this file)

## Diff summary

Planning artifacts only. No behavioural change.

## Commands run

- `git branch -d fix-correctness-review` — deleted stale, fully-merged branch (was 973e5f0)
- branch-relationship checks confirming `working-1` == `staging` == review HEAD

## Tests run

None yet (worktree not bootstrapped for tests — see next steps in session-summary).

## Tests not run

All. Bootstrap `config.json` + `google_secrets.json` before first fix.

## Current risks

- #3 (reconcile_table) and #1 (archive) touch destructive paths; validate on scratch
  hunt/sheet before trusting on real data.
- Several findings are dual-path (bang + web); fixing one path can leave the other stale.
