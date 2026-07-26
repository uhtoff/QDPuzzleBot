# Open Questions

## Blocking

None.

## Non-blocking

- **Concurrency limit on tiling.** Each job peaks around 360 MB. Nothing stops several
  uploads tiling at once. A semaphore in `media_tiling` would cap it; not added because no
  real load has demonstrated the need.
- **A separate upload cap for canvases.** `web_attachment_max_bytes` defaults to 25 MiB and
  the motivating sheet is 16.5 MB, so it fits today. A larger sheet would be refused before
  tiling ever ran.
- **Re-tiling an existing row.** `build_pyramid` is idempotent and clears a stale pyramid, but
  nothing calls it twice. A "regenerate tiles" action would be trivial to add and is the fix
  for any row stuck at `pending` after a crash.
- **Should large *evidence* images be tiled too?** Today the 16 MP rule applies to every
  upload regardless of purpose, so a huge evidence screenshot gets a pyramid that nothing
  renders. Harmless, but it means disk is spent on tiles no UI reads until B2 (and, for
  evidence, possibly never).
- **`images/` is 27 MB of sample data in the working tree.** Gitignored, so it will not be
  committed, but it is not stored anywhere durable either. Delete it or move it out when the
  canvas work is done.
- **Stray venv to remove:** `/home/russ/.local/share/virtualenvs/scratchpad-rNTSXSjM`, created
  by a `pipenv` invocation that ran from the scratchpad directory by mistake. Deleting it was
  refused by the sandbox, so it is still there. Safe to `rm -rf`.
