# Open Questions

## Non-blocking — slice 5c deferred minors (from SDD ledger, merged c5ab063)

- Task 1: minor (deferred): no test pins settings→run-once→admin order; unused _invoker param; preflight's extra "already set up" refusal for future callers
- Task 2: minor (deferred): fallback status channel may duplicate hub archive channel (cache race); partial-run line names the bang command on web runs; RUNNING guard web-only (bang/web serialised by lock only)
- Task 3: minor (deferred): census lists setup routes under UNGATED_BY_DECISION (gated by custom owner-or-admin gate; behaviour tests guard); un-set-up clone where viewer is admin appears as a hub candidate; send_invites not wrapped (500 skips audit); existing prefill doesn't filter archived; no web test of HubUnavailable passthrough
- Task 4: minor (deferred): friendlyError lacks not_a_team_hunt/discord_unavailable/forbidden; existing.hub_id/roster_role_id typed string but nullable; already_set_up view has no link; archive modal stays open after start
- Task 5: minor (deferred): user-guide sentence spliced into a long bullet; playbook says "press Archive" (button is "Archive to hub")
- Final: parked — invites-failed contradiction: Next line says "Invites have been sent." then the invites block says sending failed — Ruling: left for the user's review; next block corrects it and gives the fix command — cost if wrong: one confusing line on a rare failure
- Time a large send_invites run via the setup page (sync POST may exceed proxy timeout)
- Retry after partial setup ignores changed advanced fields — FIXED e04986b (fields locked on retry)
