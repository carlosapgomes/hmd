# Slice 001 — Extração e persistência do tipo de transporte

## 1. Identity

- **Slice:** `slice-001-extracao-persistencia`
- **Change:** `transport-badge`
- **Task:** S1 em `tasks.md`

## 2. Objective

Entregar a capacidade de ponta a ponta: o texto extraído do relatório (fonte
única `Case.extracted_text`) passa a produzir `Case.transport_type` persistido
pelo worker de PDF, com domínio fechado e ciclo de vida completo (reenvio
zera; `CLEANED` preserva).

## 3. Read first

- `AGENTS.md`
- `openspec/changes/transport-badge/proposal.md`
- `openspec/changes/transport-badge/design.md` — D1 (regex/escopo), D2 (última
  ocorrência/ordem declarada), D3 (domínio fechado), D6 (CLEANED)
- `openspec/changes/transport-badge/specs/intake-nir/spec.md` — requisito
  «Extração do tipo de transporte do relatório» (11 cenários são o contrato)
- `apps/intake/pdf_utils.py` — molde `extract_header_metadata`/`_fold_line`
- `apps/intake/tasks.py` — `process_case_documents` (write dos metadados,
  ~linhas 225–290)
- `apps/cases/models.py` — campos de metadados (`patient_age` etc.) e ponto de
  inserção de `transport_type`
- `apps/cases/closure.py` — `_CLEANED_EMPTY_VALUES` (SÓ LEITURA: o campo não
  deve entrar no dict)
- `apps/intake/tests/test_header_metadata.py` — padrão de teste dos extratores

## 4. Requirements

- **R1** — Função pura `extract_transport_type(text)` em
  `apps/intake/pdf_utils.py`: regex linha-âncorado com whitespace HORIZONTAL
  (`^[ \t]*TRANSPORTE[ \t]*:[ \t]*(.*?)[ \t]*$`, multiline, case-insensitive),
  sem delimitação de seção (design D1); normalização via `_fold_line`:
  `ambulancia basica|basica` → `basic`, `uti movel` → `mobile_icu`; última
  ocorrência vence; ausente/fora do domínio → `None`.
  *Evidência:* testes unitários cobrindo TODOS os cenários da spec intake-nir
  (básico, UTI Móvel, `BÁSICA`, desconhecido «Aéreo», label ausente, rótulo
  sozinho com valor na linha seguinte NÃO captura, múltiplas ocorrências,
  citação anterior antes da evolução atual, menção narrativa sem dois-pontos).
- **R2** — `Case.transport_type`: `CharField` com `TextChoices`
  `basic|mobile_icu`, labels «Ambulância Básica»/«UTI Móvel», `null=True`,
  `blank=True`; migration aditiva `0015_transport_type` (próximo número após
  `0014_doctor_decided_stamps`).
  *Evidência:* teste de migration aplicada com campo nulo.
- **R3** — Worker persiste: extração fora do atomic sobre o texto limpo;
  reatribuição de `current.transport_type` no MESMO write/`update_fields`
  existente dos metadados do cabeçalho.
  *Evidência:* teste pelo caminho real da task (caso processado com label fica
  populado).
- **R4** — Payload do evento de extração NÃO carrega o valor.
  *Evidência:* teste sobre o evento gravado.
- **R5** — Reenvio de documento sem label zera o campo (worker reatribui;
  sem estado stale).
  *Evidência:* teste do fluxo de reenvio.
- **R6** — `CLEANED` preserva o campo em AMBOS os modos (minimização ativa e
  retenção) — campo fora de `_CLEANED_EMPTY_VALUES`, sem alterar `closure.py`.
  *Evidência:* teste da ciência nos dois modos.

## 5. Expected blast radius

- **Prováveis:** `apps/intake/pdf_utils.py`, `apps/intake/tasks.py`,
  `apps/cases/models.py`, `apps/cases/migrations/0015_transport_type.py` (novo),
  `apps/intake/tests/test_transport_extraction.py` (novo), testes do worker e
  da ciência (extensão de arquivos existentes de `apps/intake/tests/`).
- **Sensíveis (não mudar sem escalada):** `apps/cases/closure.py` (a
  preservação é por AUSÊNCIA do dict), pipeline LLM/anonização
  (`apps/pipeline/`, `apps/anonymization/`, `apps/llm/`), FSM/estados/eventos,
  extração existente (watermark/nº/cabeçalho), `docker-compose*`, settings.
- **Comportamento anterior preservado:** extração, gate e ciclo
  `NEW → PDF_EXTRACTING → ANONYMIZING` idênticos; casos sem label processam
  normalmente com campo vazio.

## 6. Failing-before plan

- **Targeted tests (RED):** `apps/intake/tests/test_transport_extraction.py`
  (função não existe → ImportError), teste do campo no model (AttributeError),
  teste do worker (campo não populado após processar PDF com label),
  teste de ciência com `transport_type` (falha antes da persistência existir).
- **Expected failure antes da implementação:** todos os testes novos falham
  com ausência de função/campo/comportamento.

## 7. Implementation constraints

- Fixtures de texto SINTÉTICOS com o layout real do corpus — os PDFs reais de
  `/projects/dev/hmd/temp/samples/` contêm PHI e JAMAIS entram no repo.
- Sem UI neste slice (badge é o slice 002; nada de template/view).
- Sem `\s` no regex (viola contrato «mesma linha» — ver P2 da revisão).
- Não implementar antecipadamente o mapeamento CSS de badge (slice 002).

## 8. Validation

- **Focused:** `uv run pytest apps/intake/tests/test_transport_extraction.py`
  + testes do worker/ciência estendidos.
- **Project gates:** quality gate completo de `AGENTS.md` §2.
- **Runtime/verification evidence:** script descartável (fora do repo) rodando
  `extract_transport_type` sobre o texto PyMuPDF dos 9 PDFs de
  `/projects/dev/hmd/temp/samples/`; esperado 9/9 capturas — 7 `basic`,
  2 `mobile_icu`; contagens no relatório, sem conteúdo de PHI.

## 9. Acceptance criteria

- [ ] R1: todos os cenários de extração da spec passam em teste unitário.
- [ ] R2: migration 0015 aplica; campo nasce nulo; labels canônicos.
- [ ] R3: worker popula pelo caminho real, no mesmo write dos metadados.
- [ ] R4: evento de extração sem o valor do transporte.
- [ ] R5: reenvio sem label zera o campo.
- [ ] R6: ciência preserva o campo nos dois modos, `closure.py` intocado.
- [ ] Corpus real 9/9 (7 basic, 2 mobile_icu) registrado no relatório.
- [ ] Quality gate verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: variante de valor real fora do domínio
sugerir ampliação do catálogo (decisão de produto); a preservação em CLEANED
exigir tocar `closure.py`; o write do worker não comportar o campo sem mudar o
fluxo transacional; qualquer conflito spec↔código.

## 11. Evidence report

`/tmp/hmd-transport-badge-slice-001-report.md` — status
(`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), requirements→evidence, arquivos
alterados, failing-before, passing-after, comandos+exit status, contagens do
corpus, expansão de blast radius justificada, riscos.

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
`REPORT_PATH=/tmp/hmd-transport-badge-slice-001-report.md`.
