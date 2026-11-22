# Slice 002 — Badge de transporte nas três filas

## 1. Identity

- **Slice:** `slice-002-badge-filas`
- **Change:** `transport-badge`
- **Task:** S2 em `tasks.md`
- **Depende de:** slice 001 aceito (campo `Case.transport_type` persistido).

## 2. Objective

Exibir o badge do tipo de transporte nos cards das três filas (meus casos NIR,
fila médica nas duas abas, fila do agendador), com ênfase para `UTI Móvel`,
estilo neutro para `Ambulância Básica` e paridade total de rótulo/classe entre
os papéis.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/transport-badge/proposal.md`
- `openspec/changes/transport-badge/design.md` — D4 (apresentação/TextChoices/
  mapeamento de classe único)
- `openspec/changes/transport-badge/specs/intake-nir/spec.md` — requisito
  «Badge de transporte nos cards de meus casos»
- `openspec/changes/transport-badge/specs/doctor-decision/spec.md` — requisito
  «Badge de transporte nos cards da fila médica»
- `openspec/changes/transport-badge/specs/scheduling/spec.md` — requisito
  «Badge de transporte nos cards da fila do agendador»
- `apps/cases/models.py` — `transport_type` (choices do slice 001)
- `apps/intake/views.py` (~434), `apps/doctor/views.py` (~346),
  `apps/scheduler/views.py` (~232) — contexto dos cards
- `templates/intake/_my_cases_content.html`, `templates/doctor/_case_cards.html`,
  `templates/scheduler/_queue_content.html` — linha de metadados dos cards

## 4. Requirements

- **R1** — Mapeamento único de classe CSS exportado de `apps/cases/models.py`
  junto das choices (`TRANSPORT_BADGE_CLASSES`: `mobile_icu` →
  `text-bg-warning`, `basic` → `text-bg-info`).
  *Evidência:* import nas três views/módulos de renderização; teste de paridade.
- **R2** — Cards de «meus casos» (NIR): badge junto da linha de metadados com
  rótulo canônico; sem valor → sem badge, linha intacta.
  *Evidência:* testes de renderização dos 3 cenários da spec intake-nir.
- **R3** — Cards da fila médica (abas Pendentes E Decididos Hoje): mesmo
  badge.
  *Evidência:* testes de renderização dos 3 cenários da spec doctor-decision.
- **R4** — Cards da fila do agendador (abas Pendentes E Processados Hoje):
  mesmo badge.
  *Evidência:* testes de renderização dos 3 cenários da spec scheduling.
- **R5** — Paridade: rótulo sempre de `get_transport_type_display()` (ou
  equivalente do `TextChoices`), classe sempre do mapeamento único — zero
  drift de label/classe entre os três templates.
  *Evidência:* os três conjuntos de testes afirmam a MESMA classe e o MESMO
  rótulo para o mesmo `transport_type`.

## 5. Expected blast radius

- **Prováveis:** `apps/cases/models.py` (+ constante), `apps/intake/views.py`,
  `apps/doctor/views.py`, `apps/scheduler/views.py`, os 3 templates de cards,
  testes de cards das três apps (extensão de arquivos existentes, ex.:
  `apps/doctor/tests/test_queue.py`, `apps/scheduler/tests/test_queue_parity.py`).
- **Sensíveis (não mudar sem escalada):** ordenação/filtros/paginação/guards/
  partial-refresh das filas; exibição de uid (permanece proibida); demografia
  e badges existentes; presenter re-identificado (este slice não toca
  re-identificação — o valor não passa por pseudônimos).
- **Comportamento anterior preservado:** cards de casos sem transporte ficam
  byte-equivalentes ao render atual (ausência não é placeholder).

## 6. Failing-before plan

- **Targeted tests (RED):** testes de renderização das três filas com caso
  `transport_type=mobile_icu` — esperado: badge «UTI Móvel» AUSENTE no HTML
  antes da implementação (o campo nunca chega ao template).

## 7. Implementation constraints

- Sem toque no detalhe do caso médico (linha «Transporte» do card de
  identificação é o slice 003).
- Sem novo modelo/abstração de badges: uma constante + spans nos templates.
- Badge só quando o campo está populado — nunca renderizar badge vazio.

## 8. Validation

- **Focused:** `uv run pytest apps/intake/tests apps/doctor/tests
  apps/scheduler/tests` (arquivos de cards/filas tocados).
- **Project gates:** quality gate completo de `AGENTS.md` §2.

## 9. Acceptance criteria

- [ ] R1: mapeamento único em `apps/cases/models.py`, sem classes literais
      espalhadas nos templates.
- [ ] R2–R4: 3 cenários por fila (ênfase/neutro/ausente) verdes.
- [ ] R5: paridade testada — mesma classe e mesmo rótulo nas três filas.
- [ ] Cards sem transporte inalterados (nenhum placeholder novo).
- [ ] Quality gate verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: a exposição do campo exigir mudar
queries/presenters além de adicionar a chave de contexto; o layout real dos
cards não comportar o badge sem redesign; surgir exigência de mostrar
transporte em superfície fora das filas (detalhes, painel).

## 11. Evidence report

`/tmp/hmd-transport-badge-slice-002-report.md` — mesmos campos do slice 001
(status, requirements→evidence, arquivos, failing/passing-before/after,
comandos, blast radius, riscos).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in `AGENTS.md` and preserve existing
conventions; prefer the smallest correct change.
Produce failing-before evidence, implement, run focused validation, and produce
the evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and
`REPORT_PATH=/tmp/hmd-transport-badge-slice-002-report.md`.
