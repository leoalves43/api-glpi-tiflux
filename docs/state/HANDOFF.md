# Handoff

DONE: Split `glpi_tiflux.py` (~1100 lines) into `sync/` package (committed
9d96939, pushed to origin/v2), plus a 91-test stdlib-unittest suite (added 2
more after the technician-assignment change below). Business-rule change:
`definir_tecnico()` (sync/regras_negocio.py) now only assigns a technician
(Léo Alves) for mesa ARRECADAÇÃO — every other mesa's ticket is created in
Tiflux with no responsible technician. `Config.id_tecnico_sania` removed
(dead code once `definir_tecnico` stopped returning it).
`processar_chamado` skips the `atribuir_tecnico` API call entirely when
`definir_tecnico` returns `None`. Not committed yet.

NEXT: Review the diff, then commit and push. No open implementation work
beyond that. Check decisions/LOG.md's 2026-09-08 entries before touching
followup sync or technician assignment again.

RISKS:
- Tests mock all I/O (HTTP, Postgres) — they verify orchestration and request
  shape, not that the real GLPI/Tiflux APIs still behave as documented. Live
  verification is still needed after any endpoint-facing change.
- `/internal_communications` echo prevention depends entirely on the audit
  table (no API-side origin tag) — never manually delete its rows.

CONTEXT: ARCHITECTURE.md for the module map; data/audit_tables.toon for schema.
