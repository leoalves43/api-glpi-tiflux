# Architecture

Package `sync/`, entrypoint `glpi_tiflux.py` (10-line shim calling `sync.main.main`
— keep this filename; the scheduler invokes it directly). No framework. Scheduled
by the Docker container `tiflux-glpi-sync` (`docker/loop_sincronizacao.sh`: run,
then sleep 300s — never overlaps); manual: `python glpi_tiflux.py`. Env vars
override `.env` keys (`Config.carregar`); DB is remote Postgres (`DB_HOST` in `.env`, no compose override). Split into modules 2026-09-08 (see
decisions/LOG.md); each file stays under the 500-line guideline.

## Data flow

```
GLPI (REST, session-token auth)  <-->  sync/*.py  <-->  Tiflux (REST, bearer auth)
                                            |
                                            v
                                  Postgres (2 audit tables)
```

Two independent sync passes per run, both driven from `sync/main.py:main()`:

1. **Ticket creation, GLPI -> Tiflux only.** `GlpiClient.buscar_chamados_desde()`
   (sync/glpi_client.py) probes `GET /Ticket/{id}` sequentially (this GLPI
   install's `/search/Ticket` is unreliable — do not use it).
   `processar_chamado()` (sync/processamento_chamado.py) first checks
   `TifluxClient.buscar_ticket_por_chamado_glpi()` for an already-existing
   Tiflux ticket (title `"<titulo> (<id_glpi>)"`, e.g. one opened manually
   during a GLPI token outage) — if found, links it instead of creating a
   duplicate. Otherwise translates and creates the ticket in Tiflux, assigns a
   technician, uploads attachments.
2. **Followup sync, bidirectional, for already-synced open tickets.**
   `sincronizar_followups()` (sync/sincronizacao_followups.py) takes tickets whose
   Tiflux open/closed state changed in the last hour (`mudancas_status_tiflux.py`,
   one paginated `GET /tickets`) plus a rotating batch of `status='sucesso'`
   tickets, skips closed ones, and calls:
   - `sincronizar_followups_glpi_para_tiflux()` — GLPI `ITILFollowup` ->
     Tiflux `/client-answers`, always with the GLPI author's name (public only).
   - `sincronizar_followups_tiflux_para_glpi()` — Tiflux answers/internal
     communications -> GLPI `ITILFollowup`.
   Cascade status sync is otherwise Tiflux -> GLPI only, with one exception:
   if a chamado this integration cascade-closed (GLPI Solucionado) comes back
   open in GLPI while Tiflux is still closed (solution refused, or reopened
   manually), `_reabrir_tiflux_apos_recusa_glpi()` reopens the Tiflux ticket
   (`TifluxClient.reabrir_ticket`) instead of re-closing GLPI. See
   decisions/LOG.md 2026-09-16.

## Modules

| File | Responsibility |
|---|---|
| `sync/config.py` | `Config` (frozen dataclass, built once in `main()` — nothing does file I/O at import time), `carregar_credenciais`, `log()` |
| `sync/db_chamados.py` | Postgres — one row per GLPI ticket (`api_glpi_tiflux`) |
| `sync/db_followups.py` | Postgres — one row per followup (`api_glpi_tiflux_followups`) |
| `sync/glpi_client.py` | `GlpiClient` — thin wrapper over GLPI REST, owns the session headers |
| `sync/tiflux_client.py` | `TifluxClient` — thin wrapper over Tiflux REST, owns the header dicts and the mesa-validation cache |
| `sync/html_texto.py` | `html_para_texto_plano()` — GLPI HTML description -> Tiflux plain text |
| `sync/regras_negocio.py` | category->desk mapping, technician/priority lookup, requester-is-author check |
| `sync/processamento_chamado.py` | `processar_chamado()` — creates one ticket end to end |
| `sync/sincronizacao_followups.py` | the two directional sync functions + orchestrator |
| `sync/mudancas_status_tiflux.py` | picks tickets recently closed/reopened in Tiflux so the cascade runs every cycle |
| `sync/main.py` | `main()` — wiring, candidate selection, top-level logging |
| `sync/forcar_sincronizacao.py` | `python -m sync.forcar_sincronizacao --id-glpi N` — manual backup for one ticket skipped by the cron; see below |

Clients are built once per run in `main()` and passed as parameters (no globals,
no per-call re-auth); `TifluxClient` caches valid desks per instance.

## Two audit tables (Postgres, schema from `DB_SCHEMA` cred, default `siap_custom`)

Full DDL and column reference: `docs/data/audit_tables.toon`. Summary:

- `api_glpi_tiflux` — one row per GLPI ticket (`id_glpi` unique), creation only.
- `api_glpi_tiflux_followups` — one row per followup/answer, conflict key
  `(direcao, id_origem)`. Tracks both sync directions and doubles as the
  echo-prevention mechanism (see decisions/LOG.md).

## External APIs

- GLPI: session-token auth (`initSession`/`killSession`), sub-item pattern
  (`GET /Ticket/{id}/<SubItem>`), item creation via `{"input": {...}}` wrapper.
  No local spec — GLPI's own REST conventions, verified live against the test
  instance (10.3.3.68) during development.
- Tiflux: bearer auth, documented in `openapi-spec-tiflux.json` (local file,
  1.7MB — grep it, don't read it whole). Three header dicts exist for a reason:
  `_headers_get` (GET only, no Content-Type — see comment in `TifluxClient.__init__`),
  `_headers_json`, `_headers_form` (unused by followup code — followup
  POSTs use `files={"field": (None, value)}` to force real multipart; see LOG.md).

## Manual force-sync entrypoint

`sync/forcar_sincronizacao.py`, run by hand (README; the PHP interface that drove
it is paused). Reuses `processar_chamado` and both followup functions; no cascade.
`decidir_acao()` never calls `processar_chamado` when `numero_tiflux IS NOT NULL`
(even on `status='erro'`, see bug below). Its `pg_try_advisory_lock` only blocks
two forced runs on the same ticket, not the container loop.

## Known pre-existing bug (not fixed, tracked)

Technician-assignment failure in `processar_chamado` stores `status='erro'` *with*
`numero_tiflux` set; retrying re-creates (duplicates) the Tiflux ticket. So
`id_glpi -> numero_tiflux` is only reliable on `status='sucesso'` rows — keep every
read path filtering on that.
