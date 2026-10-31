# current-spec.md is missing from this archive

`.ai/active/current-spec.md` (~19 KB, the phased tranche plan for the 2026-08-30 review
remediation) was **lost during the wrap** on 2026-09-01, not omitted deliberately. The
archive step moved four of the six active files and removed the other two; `.ai/active/` is
gitignored, so git never held a copy and none is recoverable.

**Nothing load-bearing was lost.** The spec was a plan for work that is now complete,
deployed and verified, and everything durable in it is recorded elsewhere:

- **What each tranche was, and what landed** — the batch tables in
  `docs/reviews/2026-08-30-comprehensive-review.md`. Those are authoritative; the spec's
  phase list was the intent, the tables are the outcome.
- **Decisions D1–D9, and D8's correction** — `session-summary.md` in this folder, and the
  review doc's decisions sections.
- **Migration constraints** (043/044 reserved, 045 next, the pre-flight requirement) — the
  headers of `migrations/045_additional_sheets_location.sql` and
  `migrations/046_backfill_archived_additional_sheets.sql`, which carry more detail than
  the spec did and are version-controlled.
- **Per-finding Sheets research** — `sheets-tranche-research.md` in this folder.

The one thing genuinely gone is the ordering rationale for tranches that were never run as
originally planned, which the review doc supersedes anyway.

**Lesson for the next wrap:** the archive step reported "4 files moved" when `.ai/active/`
held six. Count the files before and after, and reconcile, rather than trusting the report.
Because the directory is gitignored, a wrap is the one operation in this repo that can
destroy work with no way back.
