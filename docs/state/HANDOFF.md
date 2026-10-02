# Handoff

DONE (2026-10-02):
- Cascade close/reopen every cycle (`sync/mudancas_status_tiflux.py`, commit
  1d1be78). 242 tests green; local container rebuilt, first run clean. Not pushed.
- VPS deploy (same host as Postgres) is built but STOPPED: the Caddy in front
  of the GLPI answers 302 -> /pmc/ for the VPS egress IP. Waiting on prefeitura
  TI to allow it. Local container is RUNNING meanwhile.
- VPS `.env` needs 644 (or chown to container uid): 600 -> PermissionError.

NEXT:
1. Push to GitHub (ask user first), then on VPS: `git pull`.
2. After IP is allowed: `docker compose stop` locally, then
   `docker compose up -d --build` on VPS, confirm clean run, `down` locally.
3. PHP web interface paused (2026-10-01); README documents CLI/Docker only.

RISKS:
- Two schedulers on the same DB = duplicate Tiflux tickets/followups.
- On the VPS, DB_HOST=public IP from inside the container: pg_hba/firewall must
  accept the Docker bridge (172.16.0.0/12) as source.
- GLPI técnico assign 400 ERROR_GLPI_ADD when already assigned manually — ignore.
- Remote Postgres connection has no sslmode set — consider firewall/SSL.

CONTEXT: decisions/LOG.md 2026-10-02, specs/001-docker.md.
