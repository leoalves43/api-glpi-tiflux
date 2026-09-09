# Handoff

DONE: branch main has (pushed up to 6b55f02) the "Módulo Utilizado" custom
field fix, the 207-as-success fix, and the Postgres encoding fix — see
decisions/LOG.md for details.

This session, on top of that (uncommitted): chamados were auto-flipping from
GLPI status "Novo" to "Processando (atribuído)" whenever a técnico was
assigned or a followup was created (GLPI's own default behavior on adding an
actor/interaction, not a deliberate choice). New `GlpiClient.voltar_status_para_novo()`
called after `_atribuir_tecnico_glpi` (ticket creation) and after every
successful Tiflux->GLPI `criar_followup`. User confirmed this should apply
ALWAYS, including after a cascade-reopen — no special-casing. Verified live
on real chamado #33756 that the PUT actually sticks (even 5->1, not just
2->1); side effect noted to the user (accidentally reopened that test
ticket). 162 tests green.

NEXT: Commit and push this status-reset fix. No other pending work known.

RISKS:
- The Windows Scheduled Task `GLPI-Tiflux-Sync` runs `glpi_tiflux.py` directly
  from this working directory every 5 min — uncommitted edits go live in
  production immediately. Was disabled mid-session while verifying the
  status-reset fix live, re-enabled after.
- `_mensagem_de_recusa()`'s "non-empty message = failure" is a heuristic from
  live observation, not documented GLPI behavior.
- Cascade close/reopen only trusts ITS OWN status 5 as "closed by cascade."
- Any Tiflux API response code besides 200/201/207 is still treated as
  failure; if the API ever adds another success-with-side-effects status,
  the same false-erro-then-duplicate failure mode can recur.
- `voltar_status_para_novo()` always wins over cascade-reopen's status 2 the
  moment a new followup arrives — by design (user confirmed), but means a
  reopened chamado never visibly stays "Processando" in GLPI.

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
