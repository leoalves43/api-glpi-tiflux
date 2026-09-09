# Handoff

DONE: branch v3 (pushed to origin/v3 up to c7f9cfc) has, on top of v2: followup
authorship by mesa (users_id Léo/Sania), cascade close/reopen (with required
GLPI técnico+solução preconditions, discovered live), GLPI title prefix
(`#<tiflux> - `), Tiflux author-name+timestamp prefix on synced content, and
sync scope widened to GLPI groups 21+22. Echo-prevention fix (uncommitted,
this turn): `_followups_glpi_pendentes()` now also excludes GLPI followups
authored by Léo/Sania (config.id_glpi_leo/id_glpi_sania) — those are always
the integration's own Tiflux->GLPI writes, and were leaking back to Tiflux as
duplicate messages.

Also uncommitted, this session: ticket creation now always sets the required
Tiflux custom field "Módulo Utilizado" to "Padrão" (`_montar_form_data`,
`Config.id_campo_modulo_utilizado_tiflux`/`id_opcao_modulo_utilizado_padrao_tiflux`).
Doing this exposed two real bugs, both fixed: (1) `criar_ticket()` didn't
accept HTTP 207 (the status Tiflux returns on success once `entities` is in
the request) — every creation was wrongly marked `erro`, which would have
duplicated the ticket on every retry; (2) Postgres client encoding on Windows
defaulted to cp1252 and the audit DB's own server encoding is WIN1252, so any
non-latin1 character (e.g. '✪') crashed the whole run or got rejected by the
DB — fixed with explicit `client_encoding='UTF8'` + sanitizing `mensagem`
before insert. Two real tickets (#33753/GLPI -> #361535/Tiflux,
#33756/GLPI -> #361536/Tiflux) were created for real during testing before
the 207 fix landed; recovered by hand (title prefix + técnico assignment done
manually, audit rows corrected to `sucesso`) instead of letting them retry.
156 tests green.

NEXT: Commit everything from this session (echo-prevention fix + Módulo
Utilizado field + 207 fix + encoding fix) and push. Tell the user about
already-existing duplicate answers found live on Tiflux #361478 (e.g. #31469080
echoes #31468861) — not deleted automatically, needs their decision.

RISKS:
- The Windows Scheduled Task `GLPI-Tiflux-Sync` runs `glpi_tiflux.py` directly
  from this working directory every 5 min — uncommitted edits go live in
  production immediately. Was disabled mid-session while testing the Módulo
  Utilizado field, re-enabled after the 207 fix was verified live.
- `_mensagem_de_recusa()`'s "non-empty message = failure" is a heuristic from
  live observation, not documented GLPI behavior.
- Cascade close/reopen only trusts ITS OWN status 5 as "closed by cascade."
- Any Tiflux API response code besides 200/201/207 is still treated as
  failure; if the API ever adds another success-with-side-effects status,
  the same false-erro-then-duplicate failure mode can recur.

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
