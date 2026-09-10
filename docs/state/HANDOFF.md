# Handoff

DONE: added `sync/forcar_sincronizacao.py` (manual backup entrypoint,
`python -m sync.forcar_sincronizacao --id-glpi N`) + `db_chamados.obter_estado_chamado()`,
for a new sibling PHP project `interface-web-api-glpi-tiflux` (search page +
force-sync page) to shell out to. Never re-creates a Tiflux ticket when
`numero_tiflux` is already set (any status) — only forces followups in that
case. Advisory-lock guarded against double-click/two-tab races. 187 tests
green (3 new files/additions, see decisions/LOG.md 2026-09-10 entry).

NEXT: Commit and push (this repo). On the PHP side (separate project),
finish wiring config.php to this project's credenciais.txt and confirm
pdo_pgsql is enabled before first real use.

RISKS:
- The Windows Scheduled Task `GLPI-Tiflux-Sync` runs `glpi_tiflux.py` directly
  from this working directory every 5 min — uncommitted edits go live in
  production immediately.
- `forcar_sincronizacao.py`'s advisory lock does NOT coordinate with that
  scheduled task — only with itself (two manual force-sync calls on the
  same id_glpi). A forced run and a concurrent cron pass on the same ticket
  can still race.
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
