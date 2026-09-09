# Handoff

DONE: branch v3 (pushed to origin/v3 up to c7f9cfc) has, on top of v2: followup
authorship by mesa (users_id Léo/Sania), cascade close/reopen (with required
GLPI técnico+solução preconditions, discovered live), GLPI title prefix
(`#<tiflux> - `), Tiflux author-name+timestamp prefix on synced content, and
sync scope widened to GLPI groups 21+22. Echo-prevention fix (uncommitted,
this turn): `_followups_glpi_pendentes()` now also excludes GLPI followups
authored by Léo/Sania (config.id_glpi_leo/id_glpi_sania) — those are always
the integration's own Tiflux->GLPI writes, and were leaking back to Tiflux as
duplicate messages. 153 tests green.

NEXT: Commit the echo-prevention fix and push. Tell the user about
already-existing duplicate answers found live on Tiflux #361478 (e.g. #31469080
echoes #31468861) — not deleted automatically, needs their decision.

RISKS:
- The Windows Scheduled Task runs `glpi_tiflux.py` directly from this working
  directory every 5 min — uncommitted edits go live in production immediately.
- `_mensagem_de_recusa()`'s "non-empty message = failure" is a heuristic from
  live observation, not documented GLPI behavior.
- Cascade close/reopen only trusts ITS OWN status 5 as "closed by cascade."

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
