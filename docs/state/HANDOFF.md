# Handoff

DONE: `processar_chamado()` now checks for an already-existing Tiflux ticket
(by title, `TifluxClient.buscar_ticket_por_chamado_glpi()`) before creating
one, to stop duplicating chamados that were opened manually during the
2026-09-11 GLPI token outage. See decisions/LOG.md 2026-09-14 entry. 197
tests green (2 new files/additions: tiflux_client + processamento_chamado
tests). Also reconciled `api_glpi_tiflux.numero_tiflux` in Postgres for the
8 GLPI ids duplicated by this bug (33870/33885/33896/33898/33913/33921/
33922/33923) back to the original manual Tiflux ticket, per user's request
(2026-09-14, "sim corrigir a tabela agora").

NEXT: Two things user explicitly deferred, still open:
1. The 8 duplicate Tiflux tickets themselves (361923/361924/361925/361926/
   361928/361929/361930/361931) still exist and need manual close/merge —
   only the DB link was fixed, not the Tiflux side.
2. GLPI titles for those same 8 are inconsistent (some double-prefixed like
   "#361923 - #361837 - ...", #33923 has no prefix at all) — user said DB
   only for now, titles left as-is.
Then commit this code fix (not yet committed).

RISKS:
- The Windows Scheduled Task `GLPI-Tiflux-Sync` runs `glpi_tiflux.py` directly
  from this working directory every 5 min — uncommitted edits go live in
  production immediately.
- `forcar_sincronizacao.py`'s advisory lock does NOT coordinate with that
  scheduled task — only with itself (two manual force-sync calls on the
  same id_glpi). A forced run and a concurrent cron pass on the same ticket
  can still race.
- New link-existing-ticket path depends on Tiflux's `search` query param
  staying fuzzy-but-title-inclusive; re-filtered client-side on a
  word-boundary match against `title` only, but if Tiflux ever changes what
  `search` covers, verify against a real chamado before trusting silently.
- `_mensagem_de_recusa()`'s "non-empty message = failure" is a heuristic from
  live observation, not documented GLPI behavior.
- Cascade close/reopen only trusts ITS OWN status 5 as "closed by cascade."
- Any Tiflux API response code besides 200/201/207 is still treated as
  failure; if the API ever adds another success-with-side-effects status,
  the same false-erro-then-duplicate failure mode can recur.
- `voltar_status_para_novo()` always wins over cascade-reopen's status 2 the
  moment a new followup arrives — by design (user confirmed), but means a
  reopened chamado never visibly stays "Processando" in GLPI.
- Sondagem rewind scales with confirmed-row density but is still bounded
  (`quantidade_registros_para_recuo`, default 10) — a chamado missed further
  back, or 404 across every recent confirmation, needs `forcar_sincronizacao.py`
  or manual recovery like #33769 was.

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
