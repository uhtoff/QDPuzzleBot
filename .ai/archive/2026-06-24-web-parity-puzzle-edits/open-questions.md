# Open Questions

All four design decisions the source doc deferred are now RESOLVED (user, 2026-06-24). Kept here
as a decision record — the rationale matters if the executor hits friction.

## Resolved

### Q1 (Phase 4, make_minimeta) — auth gate → ORGANISER+
The bang has NO role gate (`puzzles.py:985`). **Decision: gate the web endpoint with
`require_organiser(...)` (default `ORGANISER_ROLES` = "Organisers and up").** A deliberate
tightening: structural ops are gated to organiser+ on the web even though the bang is ungated.

### Q2 (Phase 5, convert_to_meta) — auth gate → ORGANISER+
The bang is gated `@has_any_role(*ADMIN_ROLES)` + `@has_permissions(manage_channels=True)`
(`puzzles.py:1431-1433`). **Decision: gate the web endpoint with `require_organiser(...)`
(default `ORGANISER_ROLES`).** This is LOOSER than the bang's ADMIN gate (also lets Organisers
convert from the web) — a deliberate choice for consistency with Q1 ("organiser+ for both
structural ops"). The web cannot enforce the Discord `manage_channels` permission either way
(it only resolves role names — same compromise `retag` makes).

### Q3 (Phase 6, rename) — move `safe_rename` into the service
Initial framing (service is ctx-free / Discord-agnostic) was WRONG. The service already renames
channels (`puzzle_service.py:82`), repositions them, and archives threads — it takes `guild`
throughout. The deferred-rename state machine is a pure Discord rate-limit workaround and belongs
beside those `channel.edit` calls. **Decision: relocate `safe_rename` + `_deferred_rename` + the
2 state attrs (`_pending_renames`, `_rename_tasks`) from the cog into `PuzzleService`** (no
constructor signature change — just instance attrs in `__init__`). Contained: 2 attrs, 2 methods,
2 cog call sites (`puzzles.py:2031`, `:2573`). Payoff: the web rename endpoint calls
`cog.service.rename_puzzle(...)` like every other command and inherits deferred-rename for free.

### Q4 (Phase 5, convert channel-type) — dissolved
`set_channel_type(ctx, type)` is NOT a real operation — it only writes the cog's request-scoped
cache `self.environment[ctx.message.id]['channel_type']` (`puzzles.py:292`). The web has no such
per-message cache and does NOT call it. The persistent effect (`metapuzzle=1`) is owned by the
service. Nothing to "place at edge vs service" — the bang keeps it; the web omits it.

## Blocking

(none — all resolved)
