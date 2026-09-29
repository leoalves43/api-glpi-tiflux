# Handoff

DONE (2026-09-29):
- Tiflux description header now shows GLPI priority name + contract SLA text
  (`regras_negocio.cabecalho_prioridade_glpi`), sent as HTML with <br><br>
  between priority / solicitante / descrição. GLPI entity-encoded `content`
  now decoded before tag stripping. 225 tests. See decisions/LOG.md 2026-09-29.

NEXT:
1. Test tickets: GLPI #34640/#34641/#34643/#34644 already closed. Tiflux
   #363733/#363734/#363735/#363738 sit in "Em Atendimento - Residentes" —
   user closes + logs appointments by hand (API token = API Embras user).
2. Next GLPI followup from a non-requester: confirm Tiflux shows author's name.
3. Watch next GLPI followup with attachment — lands on the answer, not ticket.
4. Watch the next real chamado outside ARRECADAÇÃO — técnico 4988 in GLPI.
5. Followups loop needs connection-per-thread/locking before parallelizing.
6. 8 duplicate Tiflux tickets from 2026-09-14 still need manual close/merge.

RISKS:
- Scheduled Task `GLPI-Tiflux-Sync` runs this working dir every 5 min —
  uncommitted edits go live immediately.
- Scan latency grows with ticket count (every ~ceil(N/50) runs); raise
  `tamanho_pagina_followups` if the backlog grows.
- `forcar_sincronizacao.py`'s advisory lock does not coordinate with that task.
- Tester who IS Léo/Sania in GLPI: their followups are skipped (anti-echo).

CONTEXT: decisions/LOG.md has full rationale per change; ARCHITECTURE.md for module map.
