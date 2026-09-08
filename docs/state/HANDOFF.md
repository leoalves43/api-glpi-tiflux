# Handoff

DONE: Split `glpi_tiflux.py` into `sync/` package + 93-test suite; technician
auto-assignment restricted to mesa ARRECADAÇÃO; `log()` crash on cp1252
console (emoji UnicodeEncodeError) fixed with `errors="replace"` fallback +
regression test. All committed and pushed to origin/v2 (up to eabd5d3).
Windows Task Scheduler job "GLPI-Tiflux-Sync" (SYSTEM, every 5 min) confirmed
healthy: LastTaskResult 0. Fixed unrelated `.gitignore` bug found this
session — `__pycache__\` had a stray trailing backslash so it never matched
pycache directories; corrected to `__pycache__/` (uncommitted).

NEXT: Commit the `.gitignore` fix. Keep an eye on
`Get-ScheduledTaskInfo -TaskName "GLPI-Tiflux-Sync"` over the next several
cycles to confirm `LastTaskResult` stays 0 long-term.

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
