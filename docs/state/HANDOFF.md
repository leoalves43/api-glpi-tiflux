# Handoff

DONE (2026-10-02):
- DB moved to remote Postgres (host only in `.env`, gitignored).
- VPS deploy (same host as Postgres) is built but STOPPED: the Caddy in front
  of the GLPI answers 302 -> /pmc/ for the VPS egress IP (the local PC IP gets
  GLPI). Waiting on prefeitura TI to allow the VPS IP. Local container is
  RUNNING meanwhile.
- VPS `.env` needs 644 (or chown to container uid): 600 -> PermissionError.

NEXT:
1. After IP is allowed: `docker compose stop` locally, then `up -d` on VPS,
   confirm clean run, then `docker compose down` locally.
2. PHP web interface paused (2026-10-01); README documents CLI/Docker only.

RISKS:
- Two schedulers on the same DB = duplicate Tiflux tickets/followups. Never
  start the local container while the VPS one runs.
- On the VPS, DB_HOST=public IP from inside the container: pg_hba/firewall must
  accept the Docker bridge (172.16.0.0/12) as source.
- GLPI técnico assign returns 400 ERROR_GLPI_ADD when another técnico already
  assigned it manually — expected, ignore.
- Remote Postgres connection has no sslmode set — consider firewall/SSL.

CONTEXT: decisions/LOG.md 2026-10-02, specs/001-docker.md.
