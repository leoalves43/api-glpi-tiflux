# Handoff

DONE (2026-09-25):
- Fixed followup scan rotation: open tickets now get a `verificacao_status`
  row (status='aberto') on every scan, so the 50-per-run rotation actually
  cycles through all tickets. Root cause of GLPI #34522 (Marcio's followup
  76873, not the requester) never reaching Tiflux — author filter was fine.
  219 tests passing. See decisions/LOG.md 2026-09-25.

NEXT:
1. Confirm GLPI followup 76873 (#34522) lands on Tiflux #363403 as an agent
   answer within ~3 cron runs (139 tickets / 50 per run).
2. Watch the next GLPI followup with an attachment — confirm it lands as a
   file on the Tiflux answer, not the ticket (from 2026-09-21, still unverified).
3. Watch the next real chamado outside ARRECADAÇÃO — técnico 4988 in GLPI.
4. Followups loop needs connection-per-thread/locking before parallelizing.
5. 8 duplicate Tiflux tickets from 2026-09-14 still need manual close/merge.

RISKS:
- Scheduled Task `GLPI-Tiflux-Sync` runs this working dir every 5 min —
  uncommitted edits go live immediately.
- Scan latency grows with ticket count (every ~ceil(N/50) runs); raise
  `tamanho_pagina_followups` if the backlog grows.
- `forcar_sincronizacao.py`'s advisory lock does not coordinate with that task.
- Tester who IS Léo/Sania in GLPI: their followups are skipped (anti-echo).

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
