# Handoff

DONE (2026-09-16): Fixed "recusar solução no GLPI não reabre no Tiflux"
(GLPI #24984) — cascade status sync was Tiflux->GLPI only. New
`TifluxClient.reabrir_ticket()` + `_reabrir_tiflux_apos_recusa_glpi()`.
208 tests green (14 new). NOT yet applied to #24984 itself — needs
`python -m sync.forcar_sincronizacao --id-glpi 24984` or self-heals on
next cron pass. Full trace: decisions/LOG.md 2026-09-16.

NEXT:
1. Followups loop needs a Postgres connection-per-thread/locking scheme
   before it can parallelize like sondagem already does — not started.
2. The 8 duplicate Tiflux tickets from the 2026-09-14 bug still need manual
   close/merge in Tiflux (DB link already fixed); their GLPI titles are
   still inconsistent — both deferred by user.

RISKS:
- Scheduled Task `GLPI-Tiflux-Sync` runs this working directory's code
  directly every 5 min — uncommitted edits go live in production immediately.
- `forcar_sincronizacao.py`'s advisory lock does not coordinate with that task.
- Cascade close/reopen only trusts ITS OWN status 5 as "closed by cascade";
  `_mensagem_de_recusa()`'s heuristic and the Tiflux-reopen retry gap (see
  LOG.md 2026-09-16) are both accepted, undocumented-upstream edge cases.

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
