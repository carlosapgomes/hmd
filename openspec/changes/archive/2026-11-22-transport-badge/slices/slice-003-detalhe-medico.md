# Slice 003 — Transporte no detalhe do caso médico

## 1. Identity

- **Slice:** `slice-003-detalhe-medico`
- **Change:** `transport-badge`
- **Task:** S3 em `tasks.md`
- **Depende de:** slice 001 aceito (campo persistido). Independente do
  slice 002 (superfícies distintas).

## 2. Objective

Exibir o tipo de transporte como linha rotulada no card de identificação do
detalhe do caso médico, junto da demografia, com placeholder «—» quando
ausente.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/transport-badge/proposal.md`
- `openspec/changes/transport-badge/design.md` — D4 (linha no detalhe)
- `openspec/changes/transport-badge/specs/doctor-decision/spec.md` — requisito
  «Transporte no detalhe do caso médico» (2 cenários)
- `apps/doctor/views.py` (contexto do detalhe, demografia `patient_age` etc.)
  e `apps/doctor/presenters.py` se o card de identificação vier do presenter
- `templates/doctor/case_detail.html` — card «Identificação do paciente»
- `apps/doctor/tests/test_detail_cards.py` — padrão de teste do detalhe

## 4. Requirements

- **R1** — Linha rotulada «Transporte» no card de identificação do detalhe
  médico, ao lado de idade/sexo/raça, com valor canônico via
  `get_transport_type_display()`.
  *Evidência:* teste de renderização com `transport_type=mobile_icu` afirmando
  «UTI Móvel» no card de identificação.
- **R2** — Transporte ausente exibe «—», como os demais campos vazios do
  card, sem erro.
  *Evidência:* teste de renderização com campo vazio.

## 5. Expected blast radius

- **Prováveis:** `apps/doctor/views.py` (ou presenter, onde o card de
  identificação é montado), `templates/doctor/case_detail.html`,
  `apps/doctor/tests/test_detail_cards.py`.
- **Sensíveis (não mudar sem escalada):** ordem dos cards (leitura clínica
  contratada pela spec doctor-decision), re-identificação/pseudônimos (o
  transporte não passa por eles), guards de acesso por subtipo, demais cards
  do detalhe.
- **Comportamento anterior preservado:** detalhe de caso sem transporte
  renderiza como hoje, com a linha a mais em «—».

## 6. Failing-before plan

- **Targeted test (RED):** teste de renderização do detalhe com
  `transport_type=mobile_icu` — esperado: linha «Transporte» AUSENTE no HTML
  antes da implementação.

## 7. Implementation constraints

- Apenas o papel doctor/admin vê esta superfície (guards existentes — não
  adicionar lógica de acesso nova).
- Não tocar nas superfícies do slice 002 (filas) nem em outros detalhes
  (NIR/agendador/painel são Non-Goals da change).

## 8. Validation

- **Focused:** `uv run pytest apps/doctor/tests/test_detail_cards.py` (e
  arquivos irmãos tocados).
- **Project gates:** quality gate completo de `AGENTS.md` §2.

## 9. Acceptance criteria

- [ ] R1: linha «Transporte» com valor canônico junto da demografia.
- [ ] R2: ausente → «—», sem erro, no padrão dos demais campos vazios.
- [ ] Ordem dos cards e guards inalterados.
- [ ] Quality gate verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: o card de identificação do detalhe
médico não for o local canônico da demografia no código atual (contradição com
a spec); a exibição exigir re-identificação (o valor nunca é pseudonimizado);
surgir demanda de mostrar transporte em outros detalhes (fora do aprovado).

## 11. Evidence report

`/tmp/hmd-transport-badge-slice-003-report.md` — mesmos campos dos slices
anteriores.

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
`REPORT_PATH=/tmp/hmd-transport-badge-slice-003-report.md`.
