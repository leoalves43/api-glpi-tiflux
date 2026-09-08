# Handoff

DONE: Bidirectional followup sync (GLPI <-> Tiflux) implemented in
glpi_tiflux.py (section 5B), `api_glpi_tiflux_followups` table created in
prod Postgres, both directions live-tested with real content. Committed
a1d675e, pushed to origin/v2.

NEXT: No open implementation work. Check decisions/LOG.md's 2026-09-08
entries before touching followup sync — several fixes came from
live-testing surprises, not the original plan.

RISKS:
- glpi_tiflux.py is ~1100 lines, one file — exceeds the 500-line guideline.
- No automated tests exist in this repo; all verification is manual/live.
- `/internal_communications` echo prevention depends entirely on the audit
  table (no API-side origin tag) — never manually delete its rows.

CONTEXT: ARCHITECTURE.md for the system map; data/audit_tables.toon for schema.
