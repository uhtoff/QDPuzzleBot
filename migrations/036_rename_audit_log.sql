-- Rename web_audit_log to audit_log.
-- The table is a general "which human did what" record, not a web-only one: the
-- fresh-server-per-hunt-team design (docs/superpowers/specs/2026-07-31-...) adds
-- Discord-side role grant/revoke rows alongside the dashboard mutations, so the
-- `web_` prefix no longer describes what the table holds.
RENAME TABLE web_audit_log TO audit_log;
