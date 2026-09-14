# Architecture

Package `sync/`, entrypoint `glpi_tiflux.py` (10-line shim calling `sync.main.main`
— keep this filename; cron invokes it directly). No framework. Run manually or
via cron: `python glpi_tiflux.py`. Split into modules 2026-09-08 (see
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
   `sincronizar_followups()` (sync/sincronizacao_followups.py) rotates through a
   batch of `status='sucesso'` tickets, skips closed ones, and calls:
   - `sincronizar_followups_glpi_para_tiflux()` — GLPI `ITILFollowup` ->
     Tiflux `/answers`, `/client-answers`, or `/internal_communications`.
   - `sincronizar_followups_tiflux_para_glpi()` — Tiflux answers/internal
     communications -> GLPI `ITILFollowup`.

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
| `sync/main.py` | `main()` — wiring, candidate selection, top-level logging |
| `sync/forcar_sincronizacao.py` | `python -m sync.forcar_sincronizacao --id-glpi N` — manual backup for one ticket skipped by the cron; see below |

`GlpiClient` and `TifluxClient` are constructed once per run (in `main()`) and
threaded through as parameters — no module-level globals, no per-call
re-authentication. `TifluxClient` caches the client's valid desks on first use
(instance attribute, not global).

## Two audit tables (Postgres, schema from `DB_SCHEMA` cred, default `siap_custom`)

Full DDL and column reference: `docs/data/audit_tables.toon`. Summary:

- `api_glpi_tiflux` — one row per GLPI ticket (`id_glpi` unique). Tracks ticket
  creation only.
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

`sync/forcar_sincronizacao.py`, driven by a separate PHP interface
(`interface-web-api-glpi-tiflux`, sibling project) for when a ticket falls
out of the automatic sondagem/rotation windows. Reuses `processar_chamado`
and both followup-direction functions directly — no business logic
duplicated in PHP, which only shells out to this script and parses its
final stdout line (JSON).

`decidir_acao()` picks the action from the audit row alone, never from
`status` in isolation — `numero_tiflux IS NOT NULL` always means "don't call
`processar_chamado`," including on `status='erro'` (the known duplication
bug above), because that column is the one that tracks whether a Tiflux
ticket actually exists. A `pg_try_advisory_lock` keyed on `(forcar_sincronizacao,
id_glpi)` blocks a double-click/two-tab race on the same ticket; it does NOT
coordinate with the Windows Scheduled Task `GLPI-Tiflux-Sync`, which has no
lock of its own.

## Known pre-existing bug (not fixed, tracked)

If technician assignment fails in `processar_chamado`, the row is stored
`status='erro'` but *with* a `numero_tiflux` already set. The retry path
re-runs full ticket creation, which can duplicate the Tiflux ticket. Effect:
`id_glpi -> numero_tiflux` is only reliable from `status='sucesso'` rows —
every read path in this codebase already respects that; keep it that way.
