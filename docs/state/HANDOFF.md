# Handoff

DONE (2026-09-30):
- Docker (Dockerfile, docker-compose.yml, docker/loop_sincronizacao.sh). Env vars
  override `.env`. 227 tests green in image; container reaches Postgres, GLPI, Tiflux.
- `.env` DB_SCHEMA typo fixed (tiflux_glpi_sinc -> tiflux_glpi_sync): 161 chamados
  (max #34668) + 752 followups already there. Loop traps SIGTERM (tested).

NEXT:
1. Container LIVE since 2026-09-30 11:52 (constraints restored via
   scripts/restaurar_constraints_auditoria.sql). First run clean.
2. GLPI #34669 linked to Tiflux #363865 (no dup), but técnico assign got 400
   earlier — check técnico on #34669 in GLPI manually.
3. PHP interface still calls host python; move to `docker compose run` later.

RISKS:
- Two schedulers on the same DB = duplicate Tiflux tickets/followups.
- Sync only runs while Docker Desktop is up (user logged in).

CONTEXT: specs/001-docker.md, plans/001-docker.md, decisions/LOG.md 2026-09-30.
