# Handoff

DONE (2026-09-21):
- Fixed followup attachments: a doc attached to a GLPI followup (by a
  requester or tech) was silently dropped — never reached Tiflux at all.
  Now sent as `files[]` alongside the answer/client-answer POST, tied to
  that specific followup instead of floating at ticket level. See
  decisions/LOG.md for the GLPI #34234/Tiflux #362601 diagnosis. 215 tests
  passing; NOT yet live-verified on a real followup with an attachment.

NEXT:
1. Watch the next GLPI followup with an attachment — confirm it lands as a
   file on the Tiflux answer, not the ticket.
2. Watch the next real chamado outside ARRECADAÇÃO — confirm técnico lands
   on 4988/"Suporte Embras" in GLPI, not the old Sania id.
3. Followups loop needs a Postgres connection-per-thread/locking scheme
   before it can parallelize like sondagem already does — not started.
4. The 8 duplicate Tiflux tickets from the 2026-09-14 bug still need manual
   close/merge in Tiflux; their GLPI titles are still inconsistent — both
   deferred by user.

RISKS:
- Scheduled Task `GLPI-Tiflux-Sync` runs this working directory's code
  directly every 5 min — uncommitted edits go live in production immediately.
- `forcar_sincronizacao.py`'s advisory lock does not coordinate with that task.
- A test chamado where the tester IS Léo in GLPI will silently skip
  publishing their own followups (anti-echo filter, `_followups_glpi_pendentes`).
- A single followup with >10 attachments still splits across the answer (first
  10) and a ticket-level fallback (rest) — not verified live, only unit-tested.

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
