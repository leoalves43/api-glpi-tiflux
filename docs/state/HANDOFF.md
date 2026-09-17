# Handoff

DONE (2026-09-17):
1. `TifluxClient.reabrir_ticket()` now sends `disapproval_reason` (was
   `json={}`) — Tiflux requires it to reopen a ticket pending review.
2. Fixed the real "masking" bug: `registrar_resultado_followup()`'s upsert
   never updated `tipo`, so the single cascade-status row per chamado
   (`id_origem=-id_glpi`) had `tipo` frozen at whatever the FIRST insert ever
   wrote — `obter_ultima_acao_cascata_sucesso()` was reading stale state
   forever. This is why a SECOND close in Tiflux re-opened the ticket
   instead of cascading the close to GLPI (live repro on #34187). Fixed by
   adding `tipo = EXCLUDED.tipo` to the `ON CONFLICT ... DO UPDATE SET`.
   #34187's row was manually repaired (user-approved one-off UPDATE); 2 more
   affected chamados found (#33898, #33753) but both dormant/closed on both
   sides — left alone, will self-correct on their next real cascade write.

NEXT:
1. Watch #34187 through one more full cycle (close in Tiflux -> cascade
   closes GLPI) to confirm the upsert fix converges correctly now that
   `tipo` updates live. Not yet re-tested after the fix.
2. The 2026-09-16 403 ("You are not allowed to review this ticket") is
   STILL UNCONFIRMED as fixed — #362498 was in Tiflux's "pending review"
   window, which per the OpenAPI spec's badges doesn't require the
   permission that blocked #34018/#362124 (fully closed, outside that
   window). Re-test `reabrir_ticket` against a fully-closed Tiflux ticket
   (e.g. #362124) to know if Tiflux actually granted the permission.
3. Followups loop needs a Postgres connection-per-thread/locking scheme
   before it can parallelize like sondagem already does — not started.
4. The 8 duplicate Tiflux tickets from the 2026-09-14 bug still need manual
   close/merge in Tiflux (DB link already fixed); their GLPI titles are
   still inconsistent — both deferred by user.

RISKS:
- Scheduled Task `GLPI-Tiflux-Sync` runs this working directory's code
  directly every 5 min — uncommitted edits go live in production immediately.
- `forcar_sincronizacao.py`'s advisory lock does not coordinate with that task.
- Cascade close/reopen only trusts ITS OWN status 5 as "closed by cascade";
  `_mensagem_de_recusa()`'s heuristic is an accepted, undocumented-upstream
  edge case.
- `_followups_glpi_pendentes()` filters out followups authored by
  `config.id_glpi_leo`/`id_glpi_sania` (anti-echo, ver
  sincronizacao_followups.py) — a real requester never collides with this,
  but a test chamado where the tester IS Léo/Sania in GLPI will silently
  skip publishing their own followups; confirmed on #34187 (refusal
  followup had to be published manually, bypassing the filter, to test the
  Tiflux side at all).

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
