# Handoff

DONE (2026-09-17):
- `definir_autor_glpi()` now always returns Léo's id (4988, renamed
  "Suporte Embras" in GLPI), independent of mesa — Sania's GLPI user was
  deactivated. All GLPI técnico assignment + Tiflux->GLPI followup
  authorship goes to 4988 now. `id_glpi_sania` kept only for the anti-echo
  filter (past followups authored as Sania). 208 tests passing; NOT yet
  live-verified on a real ticket outside ARRECADAÇÃO.
- Cascade close/reopen (`_encerrar_em_cascata`) and disapproval-reopen bugs
  fixed this session — see decisions/LOG.md for the four 2026-09-17 entries.

NEXT:
1. Watch the next real chamado outside ARRECADAÇÃO — confirm técnico lands
   on 4988/"Suporte Embras" in GLPI, not the old Sania id.
2. Followups loop needs a Postgres connection-per-thread/locking scheme
   before it can parallelize like sondagem already does — not started.
3. The 8 duplicate Tiflux tickets from the 2026-09-14 bug still need manual
   close/merge in Tiflux; their GLPI titles are still inconsistent — both
   deferred by user.

RISKS:
- Scheduled Task `GLPI-Tiflux-Sync` runs this working directory's code
  directly every 5 min — uncommitted edits go live in production immediately.
- `forcar_sincronizacao.py`'s advisory lock does not coordinate with that task.
- A test chamado where the tester IS Léo in GLPI will silently skip
  publishing their own followups (anti-echo filter, `_followups_glpi_pendentes`).

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
