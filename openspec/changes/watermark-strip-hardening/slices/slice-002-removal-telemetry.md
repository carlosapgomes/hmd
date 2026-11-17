# Slice 002 — Telemetria da remoção no evento de extração

## Contexto necessário

- Slice 001 entregou `WatermarkStripResult` (texto + `WatermarkReport`
  dataclass frozen com `tokens/mark_lines/swept_occurrences`, semântica
  fechada no design D3) e a transição do caller:
  `result = strip_watermark(raw_text, record_number=record_number)`.
- `CaseEvent.payload` é `JSONField` (`apps/cases/models.py`) — dataclass NÃO
  serializa: mandatório `dataclasses.asdict(result.report)` (dict plano).
- Evento `CASE_EXTRACTION_COMPLETED`: gravado em
  `apps/intake/tasks.py::_extract_and_decide` APENAS no ramo `gate.ok`
  (payload enxuto com `text_length`, `intake/tasks.py:277`); caso retido
  grava `CASE_GATE_MANUAL_REVIEW` sem contagens — decisão do design (Non-Goal):
  telemetria vive só no evento de conclusão.
- Suíte da task: `apps/intake/tests/test_tasks.py` com fábrica PRÓPRIA
  `_pdf_bytes` de página única (`:69-77`) — fixtures não cruzam arquivos;
  após o slice 001 a fixture já usa token de marca ≠ nº em ≥2 linhas de
  marca (forma observada).

## Goal

Payload do `CASE_EXTRACTION_COMPLETED` carrega as contagens da remoção sob a
chave `watermark` (dict plano via `asdict`); caso sem marca reporta zerado;
nenhum valor de token na telemetria `watermark` (o campo pré-existente
`record_number` permanece, inclusive no caso coincidente); docstrings da
task atualizadas.

## Deliverables

### R1 — Payload do evento (`apps/intake/tasks.py`)

- `payload["watermark"] = dataclasses.asdict(result.report)` (3 chaves
  explícitas do design D3: `tokens`, `mark_lines`, `swept_occurrences`; sem
  `model_dump` — dataclass, não pydantic).
- Sem marca nomeada → `watermark` zerado PRESENTE (uniformidade de contrato
  para telemetria; consumidor não trata ausência).

### R2 — Docstrings (`apps/intake/tasks.py` apenas)

- Docstring de `_extract_and_decide`: registrar a telemetria no payload, a
  ordem D3/R5b (bruto→strip, nº antes) e o sweep assimétrico (D2 do design:
  nº coincidente varre global, demais tokens só linhas).
- NÃO tocar `pdf_utils.py` (docstrings do módulo foram do slice 001).

### R3 — Testes (`apps/intake/tests/test_tasks.py`)

Quatro testes (RED esperado dos três primeiros: chave `watermark` ausente;
o quarto pinna a decisão de contrato do P1 da revisão):

- `test_extraction_completed_event_carries_watermark_counts`: PDF com marca
  (fixture pós-slice-001: token ≠ nº em 2 linhas de marca) → evento traz
  `watermark` com contagens > 0 coerentes e `extracted_text` sem a marca
  (RED: chave `watermark` ausente no payload hoje).
- `test_extraction_completed_event_watermark_zero_without_mark`: PDF sem
  marca → `watermark` com as 3 chaves zeradas (RED: chave ausente).
- `test_watermark_payload_has_no_token_value`: com marca de token T ≠ nº,
  `json.dumps(payload["watermark"])` NÃO contém `\bT\b` — a proibição de
  valores é da TELEMETRIA (o campo pré-existente `record_number` carrega o nº
  por design anterior a este change e fica fora dela; comentário no teste
  documenta a exceção do caso coincidente).
- `test_watermark_counts_when_mark_equals_record_number`: marca coincidente
  com o nº (token em ≥2 linhas de marca + `Código: <nº>`) → `watermark` com
  contagens coerentes (tokens=1, mark_lines≥2, swept_occurrences conta a
  citação do `Código:` varrida), `record_number` retém o nº (exceção
  deliberada, documentada) e `extracted_text` sem o token.

## Acceptance criteria

- Eventos `CASE_EXTRACTION_COMPLETED` (com e sem marca) carregam
  `watermark` com as 3 chaves de contagem, sem valores de token NA TELEMETRIA
  (o `record_number` pré-existente permanece, inclusive no caso coincidente).
- `CASE_GATE_MANUAL_REVIEW` permanece sem `watermark` (Non-Goal do design).
- Quality gate completo verde.

## Out of Scope

- UI/painel consumindo as contagens (telemetria bruta na trilha apenas);
  mudanças em `WatermarkReport` (dono do slice 001); evento de retenção do
  gate; anonimização/pipeline.

## Allowed incidental files

Nenhum de CÓDIGO. Desvio aprovado em revisão (P1 → decisão de contrato): os
próprios artefatos `slices/slice-002-removal-telemetry.md` e
`specs/intake-nir/spec.md` foram editados durante o slice para registrar o
escopo da proibição de valores (telemetria `watermark` × `record_number`
pré-existente) — manutenção de planejamento, não código.

## Verification (RED → GREEN)

```bash
# RED: os três testes de R3 falham (chave watermark ausente)
uv run pytest apps/intake/tests/test_tasks.py -q
# GREEN + gate completo:
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

## Expected files

- apps/intake/tasks.py
- apps/intake/tests/test_tasks.py
