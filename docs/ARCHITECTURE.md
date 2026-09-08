# Architecture

Single-file cron script, `glpi_tiflux.py` (~1100 lines — over the 500-line
guideline; see decisions/LOG.md 2026-09-08 for why it hasn't been split yet).
No package, no tests, no framework. Run manually or via cron: `python glpi_tiflux.py`.

## Data flow

```
GLPI (REST, session-token auth)  <-->  glpi_tiflux.py  <-->  Tiflux (REST, bearer auth)
                                            |
                                            v
                                  Postgres (2 audit tables)
```

Two independent sync passes per run, both inside `main()` (glpi_tiflux.py:1032):

1. **Ticket creation, GLPI -> Tiflux only.** `buscar_chamados_desde()` (glpi_tiflux.py:305)
   probes `GET /Ticket/{id}` sequentially (this GLPI install's `/search/Ticket` is
   unreliable — do not use it). `processar_chamado()` (glpi_tiflux.py:677) translates
   and creates each ticket in Tiflux, assigns a technician, uploads attachments.
2. **Followup sync, bidirectional, for already-synced open tickets.**
   `sincronizar_followups()` (glpi_tiflux.py:987) rotates through a batch of
   `status='sucesso'` tickets, skips closed ones, and calls:
   - `sincronizar_followups_glpi_para_tiflux()` (glpi_tiflux.py:804) — GLPI
     `ITILFollowup` -> Tiflux `/answers`, `/client-answers`, or `/internal_communications`.
   - `sincronizar_followups_tiflux_para_glpi()` (glpi_tiflux.py:923) — Tiflux
     answers/internal communications -> GLPI `ITILFollowup`.

## Module sections (in file order)

| # | Section | Key functions |
|---|---|---|
| 1 | CONFIGURAÇÃO | credentials from `credenciais.txt`, all tunable constants |
| 2 | POSTGRES — auditoria | `registrar_resultado`, `registrar_resultado_followup`, candidate queries |
| 3 | GLPI — auth & busca | `autenticar_glpi`, `buscar_chamados_desde`, `obter_followups_glpi`, `criar_followup_glpi` |
| 4 | TRADUÇÃO (regras de negócio) | category->desk mapping, requester/technician resolution, HTML->plaintext |
| 5 | PROCESSAMENTO DE UM CHAMADO | `processar_chamado` — creates one ticket end to end |
| 5B | SINCRONIZAÇÃO DE FOLLOWUPS | the two directional sync functions + orchestrator |
| 6 | MAIN | `main()` |

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
  `headers_tiflux_get` (GET only, no Content-Type — see comment at glpi_tiflux.py:88),
  `headers_tiflux_json`, `headers_tiflux_form` (unused by followup code — followup
  POSTs use `files={"field": (None, value)}` to force real multipart; see LOG.md).

## Known pre-existing bug (not fixed, tracked)

If technician assignment fails in `processar_chamado`, the row is stored
`status='erro'` but *with* a `numero_tiflux` already set. The retry path
re-runs full ticket creation, which can duplicate the Tiflux ticket. Effect:
`id_glpi -> numero_tiflux` is only reliable from `status='sucesso'` rows —
every read path in this codebase already respects that; keep it that way.
