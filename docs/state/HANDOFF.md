# Handoff

DONE: Split `glpi_tiflux.py` into `sync/` package + 93-test suite (committed
9d96939, 0f8ed1f, fac2da3, pushed to origin/v2). Technician-assignment change:
only mesa ARRECADAÇÃO gets an auto-assigned technician (Léo Alves); every
other mesa's ticket is created in Tiflux unassigned. Windows Task Scheduler
job "GLPI-Tiflux-Sync" created (runs as SYSTEM, every 5 minutes, working
directory set so `credenciais.txt` resolves) — verified with a manual trigger
(LastTaskResult 0). While verifying, found and fixed a live crash: `log()`
raised `UnicodeEncodeError` on the emoji in `buscar_chamados_desde()`'s
unconditional final log line, on this machine's cp1252 console — that line
runs on every invocation, so it would have failed the 5-minute job on
essentially every run. `log()` now catches the encoding error and re-encodes
with `errors="replace"` instead of crashing the process. Regression test
added (`tests/test_config.py`). Not committed yet.

NEXT: Review the diff, then commit and push. Watch the scheduled task for a
few cycles (`Get-ScheduledTaskInfo -TaskName "GLPI-Tiflux-Sync"`) to confirm
`LastTaskResult` stays 0.

RISKS:
- Tests mock all I/O (HTTP, Postgres) — they verify orchestration and request
  shape, not that the real GLPI/Tiflux APIs still behave as documented. Live
  verification is still needed after any endpoint-facing change.
- `/internal_communications` echo prevention depends entirely on the audit
  table (no API-side origin tag) — never manually delete its rows.
- Log output on this machine still garbles accented characters (mojibake)
  under cp1252 — cosmetic only, doesn't affect the Postgres audit trail, not
  fixed (would need reconfiguring stdout encoding, out of scope for the crash fix).

CONTEXT: ARCHITECTURE.md for the module map; data/audit_tables.toon for schema.
