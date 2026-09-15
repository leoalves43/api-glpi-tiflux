# Handoff

DONE: `processar_chamado()` now checks for an already-existing Tiflux ticket
(by title, `TifluxClient.buscar_ticket_por_chamado_glpi()`) before creating
one, to stop duplicating chamados that were opened manually during the
2026-09-11 GLPI token outage. See decisions/LOG.md 2026-09-14 entry. Also
reconciled `api_glpi_tiflux.numero_tiflux` in Postgres for the 8 GLPI ids
duplicated by this bug (33870/33885/33896/33898/33913/33921/33922/33923)
back to the original manual Tiflux ticket, per user's request (2026-09-14,
"sim corrigir a tabela agora").

DONE (2026-09-15): `GlpiClient`/`TifluxClient` now reuse a `requests.Session()`
per instance instead of a fresh TCP/TLS connection per call; every HTTP call
now has a 30s `timeout` (config.timeout_http_segundos); `buscar_chamados_desde()`'s
sondagem now probes in parallel batches (ThreadPoolExecutor, default 10,
config.tamanho_lote_sondagem) instead of one ID at a time. All perf work on
"execução muito demorada" (see decisions/LOG.md 2026-09-15 entries). 194 tests
green.

NEXT:
1. Followups loop (`sincronizar_followups`, up to 50 chamados/run x ~3-6
   sequential calls each) is likely the bigger remaining cost but needs a
   Postgres connection-per-thread or write-locking scheme first (shared
   psycopg2 `conn` isn't safe for concurrent multi-thread writes) — deferred,
   not started.
2. Confirm in production that the parallel sondagem doesn't trip anything on
   the GLPI side (rate limiting, session contention) — only tested against
   fakes locally, never run live yet.
3. The 8 duplicate Tiflux tickets themselves (361923/361924/361925/361926/
   361928/361929/361930/361931) still exist and need manual close/merge —
   only the DB link was fixed, not the Tiflux side (deferred by user).
4. GLPI titles for those same 8 are inconsistent (some double-prefixed like
   "#361923 - #361837 - ...", #33923 has no prefix at all) — user said DB
   only for now, titles left as-is (deferred by user).
Then commit these code changes (not yet committed).

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
- Sondagem now fires up to `tamanho_lote_sondagem` (default 10) concurrent
  GET requests at the GLPI instance instead of one at a time — never
  validated against real GLPI load, only against fakes; if this GLPI install
  can't handle it, lower `Config.tamanho_lote_sondagem` (no code change).

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
