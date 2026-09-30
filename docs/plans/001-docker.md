# Plano 001 — Docker (spec: docs/specs/001-docker.md)

## Delta de arquitetura
`Config.carregar` passa a aceitar env vars sobrepondo o `.env`. O compose define
`DB_HOST=host.docker.internal` (Postgres publicado no host; sem mexer em redes).
O agendamento sai do Windows Task Scheduler e vira um loop `run; sleep 300` no container.

## Riscos
- Se o agendador do Windows também rodar contra o mesmo banco, as execuções
  se sobrepõem e duplicam tickets no Tiflux. Desative-o antes de ligar o container.
- GLPI/Tiflux precisam ser alcançáveis pela rede NAT do Docker Desktop (VPN?).

## Tarefas
- [x] 1. Env var sobrepõe `.env` — `sync/config.py`, `tests/test_config.py` — pytest verde.
- [x] 2. Imagem + compose — `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `docker/loop_sincronizacao.sh` — build ok, pytest na imagem.
- [x] 3. Checagem de conectividade — sem arquivos — `SELECT 1` + GET no GLPI via `compose run`.
- [x] 4. Docs — `README.md`, `docs/ARCHITECTURE.md`, `docs/INDEX.md`, `docs/decisions/LOG.md`, `docs/state/HANDOFF.md`.
