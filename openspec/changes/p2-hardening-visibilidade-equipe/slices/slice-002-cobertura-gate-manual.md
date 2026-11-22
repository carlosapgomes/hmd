# Slice 002 — Cobertura de teste do gate e precisão do manual

## 1. Identity

- Slice: `slice-002-cobertura-gate-manual`
- Change: `p2-hardening-visibilidade-equipe`
- Task: `tasks.md` S2

## 2. Objective

Quitar os itens de cobertura e documentação (2, 3 e 4 da change): asserção de negação alheia no partial, teste dedicado de `AWAITING_NIR_ACK` no gate de PDF e correção das duas frases do manual do agendador.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/p2-hardening-visibilidade-equipe/proposal.md` e `design.md` — D2 (fixture via fluxo real) e D3 (manual preciso)
- `apps/scheduler/views.py` — `_scheduler_can_view_pdf` (:140-152) e `PDF_ALLOWED_STATUSES` (:128)
- `apps/scheduler/tests/test_processed_today.py` (:185-199, partial sem a asserção de negação)
- `apps/scheduler/tests/test_detail_cards.py` (padrão dos testes de gate; teste R2 da negativa médica como modelo)
- `templates/accounts/manual.html` (:270-271, frases «a ninguém»); `apps/accounts/tests/test_manual.py` (marcadores)
- Specs de referência (contrato já vigente): `openspec/specs/scheduling/spec.md` («Visão do agendador limitada ao necessário», «Histórico do agendador»), `openspec/specs/pdf-original-viewer/spec.md`

## 4. Requirements

- R1: o teste invertido de «Processados Hoje» assevera a NEGAÇÃO alheia também no partial htmx (mesma asserção da página cheia, no fragmento).
- R2: teste dedicado do gate em `AWAITING_NIR_ACK`: caso com decisão de agendamento no estado → cards/PDF visíveis à equipe; e (se o estado for alcançável pelo fluxo real para negativa médica sem decisão) negativa médica em `AWAITING_NIR_ACK` → sem cards + 404; se inalcançável, cobrir o lado positivo e documentar a razão no relatório.
- R3: manual do agendador corrige as duas frases — a restrição de PDF/texto em negativa médica direta vale para a EQUIPE DE AGENDAMENTO (não «a ninguém») e a disponibilidade menciona o gate de estados permitidos (reaberto/encerrado fora); marcadores do `test_manual.py` ajustados se citarem o texto.

## 5. Expected blast radius

- `apps/scheduler/tests/test_processed_today.py`, `apps/scheduler/tests/test_detail_cards.py` (ou irmão apropriado), `templates/accounts/manual.html`, `apps/accounts/tests/test_manual.py`.
- **Não muda**: produção (zero mudança de código de produção neste slice), specs, gate `_scheduler_can_view_pdf`.

## 6. Failing-before plan

Failing-before evidence:
- R1: a asserção nova no partial falha ANTES de qualquer edição se aplicada contra o estado atual? Não — o comportamento já existe (é lacuna de cobertura): a evidência failing-before é a AUSÊNCIA da asserção (documentar no relatório: teste novo de regressão sobre comportamento vigente, RED demonstrável por mutação pontual — opcionalmente executar uma mutação local temporária do gate para provar que o teste pega, revertida em seguida, com diff no relatório).
- R3: assert das frases novas contra o manual atual → falha (RED genuíno de documentação).

## 7. Implementation constraints

- Zero mudança de produção (se o teste R2 revelar bug real do gate: STOP + `BLOCKED_NEEDS_DECISION`).
- Mutação temporária (se usada) nunca pode ser commitada — diff antes/depois no relatório.

## 8. Validation

**Focused** — `uv run pytest apps/scheduler/tests/test_processed_today.py apps/scheduler/tests/test_detail_cards.py apps/accounts/tests/test_manual.py -q`
**Gates** — `AGENTS.md` §2 completos ao concluir.

## 9. Acceptance criteria

- [ ] R1: negação alheia asserida no partial (teste)
- [ ] R2: teste AWAITING_NIR_ACK dedicado (positivo; negativo se alcançável, senão razão documentada)
- [ ] R3: manual corrigido (2 frases) + testes de manual verdes
- [ ] Zero diff de produção; gate §2; failing-before/parity documentado no relatório

## 10. Escalation conditions

`BLOCKED_NEEDS_DECISION` se o teste R2 expuser comportamento real divergente do gate vigente.

## 11. Evidence report

`/tmp/hmd-p2h-002-report.md` (formato da casa).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce the required failing-before/parity evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=/tmp/hmd-p2h-002-report.md`.
