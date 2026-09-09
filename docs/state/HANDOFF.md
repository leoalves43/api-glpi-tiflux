# Handoff

DONE (branch v3, pushed to origin/v3 up to 28f14ce; this session's work below is
uncommitted):
1. Followup authorship fix (commit 6440843): Tiflux->GLPI followups send
   `users_id` (mesa ARRECADAÇÃO -> Léo GLPI id 4988, any other mesa -> Sania
   GLPI id 4816), resolved from the ticket's CURRENT Tiflux mesa.
2. Cascading close/reopen (commit 716a7bd): Tiflux ticket closed/canceled
   (`is_closed=True`) -> syncs pending followups, then closes GLPI (status 5
   Solucionado). Reopened in Tiflux while GLPI sits at exactly status 5 ->
   reopens GLPI too (status 2, Processando). Manual GLPI closures at any
   other status are never touched.
3. GLPI title prefix (commit 28f14ce): after a chamado is created in Tiflux,
   the GLPI ticket's title is prefixed with `#<numero_tiflux> - `.
4. Technician-at-creation + cascade-close técnico/solução prep (commit
   bebbee8): see LIVE-VERIFIED section below, same commit.

NOT YET COMMITTED — new this session:
5. Tiflux author name + timestamp prefix on synced content: every followup
   (public answer or internal communication) synced Tiflux->GLPI, and the
   solution content used when cascade-closing, now gets prefixed with
   `<strong>{nome}</strong> ({data} {hora})<br><br>` before the original
   content — `sincronizacao_followups._prefixar_autor_tiflux()` /
   `_formatar_data_hora_brasilia()`. This is purely a visual/text marker of
   WHO ACTUALLY WROTE IT IN TIFLUX; the real GLPI authorship (`users_id` on
   the followup, or the assigned técnico) still always follows the mesa rule
   (Léo/Sania via `definir_autor_glpi`) — that part of the design is
   unchanged, user was explicit about it. Date/time source: `answer_time`
   (public answers) or `created_at` (internal communications), both UTC from
   Tiflux, converted to Brasília (UTC-3) by fixed offset (no DST handling —
   Brazil hasn't used DST since 2019, so this is fine going forward but
   would need revisiting if that policy ever changes). Missing author falls
   back to "Desconhecido"; missing/unparseable timestamp just omits the
   `(data hora)` part instead of failing. 148 tests, all green.
   Live-verified on real chamado #33724 / Tiflux #361478 (mesa SUPRIMENTOS):
   José Augusto's real answer synced with content prefixed
   `<strong>José Augusto Rodrigues</strong> (09/09/2026 11:26)<br><br>...`,
   `users_id` on the followup still 4816 (Sania, per mesa rule) — confirms
   author display and real authorship are correctly decoupled.

IMPORTANT OPERATIONAL FACT discovered this session: the Windows Scheduled
Task `GLPI-Tiflux-Sync` runs `python glpi_tiflux.py` directly from THIS
working directory (`C:\Users\LeoAlves43\Desktop\api-glpi-tiflux`) every 5
minutes — not from a separately deployed copy. That means every code change
made in this session, committed or not, has been executed live against
production GLPI/Tiflux on every 5-minute tick since it was saved, across ALL
already-synced chamados (not just the ones manually tested here). User was
told and explicitly chose to keep working this way rather than pause the
task. Worth remembering for future sessions on this repo: uncommitted edits
here are not "safe to experiment" — they go live within 5 minutes.

LIVE-VERIFIED THIS SESSION (real chamados #33630, #33733, #33736 on the
production GLPI/Tiflux — not just mocks) — and found + fixed a real bug in
the process:
- **Bug found and fixed**: `PUT /Ticket/{id}` on this GLPI instance ALWAYS
  returns HTTP 200/201, even when a business rule rejects the field change —
  the rejection only shows up as a non-empty `message` in the response body
  (e.g. `[{"33733":true,"message":"Técnico atribuído é obrigatório antes do
  chamado ser solucionado/fechado"}]`). The original `encerrar_chamado()` /
  `atualizar_titulo()` treated any 200/201 as success, so a rejected status
  change would have silently no-op'd forever. Fixed: new module-level
  `_mensagem_de_recusa()` (sync/glpi_client.py) inspects the body; a non-empty
  `message` now counts as failure even on HTTP 200. Caught live, not by the
  mocked tests — mocks can't fake this GLPI quirk unless told to.
- **Bug found and fixed**: this GLPI instance also refuses to accept status
  Solucionado/Fechado unless the ticket already has (a) an assigned
  technician (`Ticket_User` type=2) and (b) at least one `ITILSolution`
  registered — confirmed by triggering both rejections live, then manually
  supplying each and confirming status 5 finally stuck. Fixed with two new
  pieces, both keyed off the same `definir_autor_glpi()` mesa rule already
  used for followup authorship:
  - `processamento_chamado.py`: new `_atribuir_tecnico_glpi()` now assigns
    a GLPI technician (`GlpiClient.atribuir_tecnico()`, POST `/Ticket_User`
    type=2) right when the chamado is created — mesa ARRECADAÇÃO -> Léo GLPI
    id 4988, else -> Sania GLPI id 4816. Same "log warning, don't block"
    failure mode as the title prefix (ticket already exists in Tiflux by
    this point; marking 'erro' would duplicate it on reprocess).
  - `sincronizacao_followups.py`: new `_encerrar_em_cascata()` runs before
    the status-5 attempt. It's idempotent on retries: `tecnico_atribuido()`
    /`solucao_registrada()` (both new `GlpiClient` GETs) are checked first,
    so a technician/solution already present (from ticket creation, or from
    a previous partially-failed retry) is never re-created. Solution content
    = the most recent PUBLIC answer (`/answers`, picked by max `answer_time`)
    from the Tiflux ticket — user's explicit choice, not internal
    communications, and not "most recent of either type". Falls back to a
    fixed message (`"Chamado encerrado no Tiflux, sem resposta pública
    registrada."`) when there are no public answers at all.
- End-to-end live confirmation on #33736/Tiflux #361499: created via
  `processar_chamado` -> title prefixed, technician auto-assigned (4988) at
  creation, confirmed via `GET`. Posted a real public answer in Tiflux,
  closed the ticket there, ran `_sincronizar_chamado_aberto` directly (single
  chamado, not the full batch) -> pending answer synced to GLPI first, then
  technician check skipped (already assigned), solution created with content
  matching the Tiflux answer exactly (verified via `GET
  /Ticket/33736/ITILSolution`), status flipped 2 -> 5. Matches the design
  exactly.
- Followup authorship (#33733): posted a private followup directly with
  `users_id=4816` (Sania) via `GlpiClient.criar_followup`, read it back —
  GLPI stored `users_id: 4816` exactly as sent, not overwritten by the
  session user. Confirms the item 1 risk from earlier in this file is
  resolved; no longer a "NEXT" item.
139 tests, all green.

NEXT:
- Commit this session's work (author-name/timestamp prefix on synced
  content). Everything in item 5 above is implemented, live-verified, and
  uncommitted as of this handoff — but is ALREADY RUNNING IN PRODUCTION per
  the operational fact above, so committing is bookkeeping, not a deploy
  step.
- Chamados synced to Tiflux BEFORE this session's technician-assignment
  change won't have a GLPI technician yet — their first cascade-close
  attempt will now auto-assign one via the same idempotent check in
  `_encerrar_em_cascata()`, so they self-heal on the next followup pass. No
  action needed, just don't be surprised seeing `atribuir_tecnico` fire for
  "old" chamados the first time they close.
- Carry over: commit the `.gitignore` fix from the earlier v2 session (still
  uncommitted there), and keep checking `LastTaskResult` on the scheduled
  task.

RISKS:
- `definir_autor_glpi()` now backs THREE different decisions, all "who
  represents this mesa in GLPI" but at different moments — followup
  authorship (Tiflux->GLPI), technician assignment at ticket creation, and
  technician assignment (if missing) right before cascade-closing. Don't
  read it as followup-specific anymore; see its docstring.
- Cascade close/reopen only recognizes ITS OWN status 5 as "closed by
  cascade" — if a human closes a ticket at status 5 manually for unrelated
  reasons, and the Tiflux ticket happens to be open, the next followup pass
  will "reopen" it back to status 2. Narrow but real; flagged, not fixed.
- `atualizar_titulo()` always prefixes unconditionally — if `_processar()`
  were ever retried on a chamado whose GLPI title already got prefixed (not
  possible today since a title-update failure no longer raises), it would
  double-prefix. No guard added since it can't happen with current control
  flow.
- `obter_ticket()` (Tiflux) adds one extra GET per followup-sync pass per
  open chamado; if that call fails, mesa resolves to `None` (falls back to
  Sania authorship/technician) and the cascade close/reopen check is skipped
  entirely for that pass (retried next time).
- `_mensagem_de_recusa()`'s heuristic ("non-empty `message` = failure") is
  based on every rejection observed live having a message and every success
  having an empty one — but it's still a heuristic, not documented GLPI
  behavior. If a future GLPI update adds a non-blocking informational
  message on success, this would misreport success as failure (safe
  direction — retries, doesn't silently no-op — but noisy).
- `/internal_communications` echo prevention depends entirely on the audit
  table (no API-side origin tag) — never manually delete its rows.
- Log output on this machine still garbles accented characters (mojibake)
  under cp1252 — cosmetic only, doesn't affect the Postgres audit trail.

CONTEXT: ARCHITECTURE.md for the module map; data/audit_tables.toon for schema.
