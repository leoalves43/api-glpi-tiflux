# Handoff

DONE: Split `glpi_tiflux.py` (~1100 lines) into `sync/` package per
CLAUDE.md's style rules (500 lines/file, 4-20 lines/function, no module-scope
I/O, DI over globals). `glpi_tiflux.py` is now a 10-line shim — cron path
unchanged. No SQL text changed (AST-diffed against pre-split file). Added
`tests/` (stdlib unittest, no new dependency, 89 tests, all green):
pure functions (html_texto, regras_negocio), `GlpiClient`/`TifluxClient`
(HTTP mocked via named fakes in `tests/fakes.py`, no real network),
`db_chamados`/`db_followups` (Postgres mocked via `FakeConnection`), and the
orchestration in `processamento_chamado`/`sincronizacao_followups` (GLPI/Tiflux
clients mocked via named fakes in `tests/fake_clients.py`, matching their
public interface so orchestration logic is tested independent of HTTP
details). Not committed yet.

NEXT: Review the diff, then commit. No open implementation work beyond that.
Check decisions/LOG.md's 2026-09-08 entries before touching followup sync —
several fixes came from live-testing surprises, not the original plan.

RISKS:
- Tests mock all I/O (HTTP, Postgres) — they verify orchestration and request
  shape, not that the real GLPI/Tiflux APIs still behave as documented. Live
  verification is still needed after any endpoint-facing change.
- `/internal_communications` echo prevention depends entirely on the audit
  table (no API-side origin tag) — never manually delete its rows.

CONTEXT: ARCHITECTURE.md for the module map; data/audit_tables.toon for schema.
