# Open Questions

## Blocking

- None. (The tile-seam content-loss bug found 2026-07-27 is **fixed** — see the design
  doc's "Known bugs" section. It still wants a look during the outstanding B2b browser
  pass, but as confirmation, not as a blocker.)

## Non-blocking

- **Does an informs edge imply a Capstone? — raised 2026-07-27, parked, no code
  changed.** Prompted by "when you create a puzzle with an informs edge that puzzle
  should be a capstone surely?". Findings, so they are not re-derived later:
  - A capstone is a property of a **group**, not a puzzle: `group.type == "Capstone"`,
    with member puzzles carrying `informs` edges *at* it. `convert_to_capstone` flips a
    meta's feeder edges `feeds → informs`. A capstone is fed by many.
  - A region's `informs` **role is not a graph edge** — B2a: "an edge joins two puzzles,
    a region is not one". `create_puzzle_from_region` (`bot/web/actions.py` ~2438) makes
    a plain puzzle in the chosen round and logs evidence; no edge is created. Making that
    puzzle a capstone would build an empty Capstone category around a new leaf puzzle.
  - Informs edges are auto-created only in `link_informs_for_evidence`
    (`puzzle_service.py` ~2993), and only when the target is **already** a group target —
    otherwise `group is None` and it returns. So it never creates a capstone either.
  - **The plausible real gap:** a `Metapuzzle` group that gains an informs edge via
    evidence routing is a capstone by the codebase's own definition — `is_informed()`
    (`puzzle_service.py:182`) says "a group with any informs-edge member is a 'capstone'
    in the design vocabulary" — but its `type` stays `Metapuzzle`, so glyph and behaviour
    stay meta. `is_informed()` is also **dead code: defined, called from nowhere.**
    Whether to retype such groups, and whether to delete or wire up `is_informed`, is the
    open decision.

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
- **User feedback on the canvas, 2026-07-27 — 5 of 7 done.** Seven items logged in the
  design doc's "Future enhancements" section. **Done:** the explicit no-link option in
  `RegionModal`; the multi-line clue note (entry field, inline editor, OCR, and display);
  highlight-regions-by-puzzle; in-place evidence image replacement; and the canvas source
  label on both the web board and the Sheets table. **Remaining, both needing a design
  conversation before code:** overlapping-region shading behaviour, and freehand/line/
  text-box annotations. See
  `docs/superpowers/specs/2026-07-26-canvas-image-evidence-design.md#future-enhancements-requested-2026-07-27-not-started`
  for detail and file references.
