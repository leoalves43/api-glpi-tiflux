# Handoff

DONE (2026-10-02):
- DB moved to remote Postgres (host only in `.env`, gitignored). Compose no
  longer overrides DB_HOST — container recreated with --force-recreate.
- First run on remote DB clean: 2 created (#34750, #34753), 57 ignored, 0 errors.
  227 tests green. Old local Postgres container `Postgres` is stopped.

NEXT:
1. PHP web interface paused (2026-10-01); README documents CLI/Docker only.

RISKS:
- Two schedulers on the same DB = duplicate Tiflux tickets/followups. Never
  restart the old local Postgres + a sync pointing at it.
- GLPI técnico assign returns 400 ERROR_GLPI_ADD when another técnico already
  assigned it manually — expected, ignore.
- Remote Postgres connection has no sslmode set — consider firewall/SSL.

CONTEXT: decisions/LOG.md 2026-10-02, specs/001-docker.md.
