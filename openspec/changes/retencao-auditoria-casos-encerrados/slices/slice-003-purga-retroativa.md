# Slice 003 — Purga retroativa dos dados retidos

**Pré-requisito**: slice 001 (semântica minimizante incondicional em
`_clean_acknowledged_clinical_data` e modo de retenção no ar).

```yaml
expected_files:
  - apps/cases/closure.py
  - apps/cases/events.py
  - apps/cases/management/commands/purge_closed_case_data.py
  - apps/cases/tests/test_purge_closed_case_data.py
  - apps/dashboard/event_labels.py
  - apps/dashboard/tests/test_event_labels.py
```

## Contexto necessário

- Política temporária do slice 001: encerramentos em modo retenção deixam
  campos clínicos/LLM populados e rows/arquivos de documentos e anexos em
  casos `CLEANED`.
- `apps/cases/closure.py`: semântica da disposição minimizante
  (`_CLEANED_EMPTY_VALUES`, `_clean_acknowledged_clinical_data` —
  INCONDICIONAL por D2, delete de rows, arquivos best-effort em
  `on_commit` após coleta pré-delete) — a purga REUSA essa função/ssemântica
  via serviço no MESMO módulo (D4).
- `apps/cases/events.py`: `CaseEventType` (TextChoices, conjunto fechado,
  append-only na trilha) — extensão aditiva; receivers de
  `CaseEvent.post_save` (cases/accounts/pipeline/attachments/anonymization)
  filtram por `event_type` — tipo novo não dispara nada.
- `apps/dashboard/event_labels.py`: mapa de rótulos legíveis cobrindo 100%
  do enum (anti-drift testado em `apps/dashboard/tests/test_event_labels.py`)
  + `event_badge_css`.
- Molde de management command: `apps/anonymization/benchmark.py`,
  `apps/llm/management/commands/seed_prompts.py` (saída legível,
  idempotente).

## Goal

Comando de gerência que minimiza retroativamente os casos `CLEANED`
retidos, quando o dono encerrar a fase de retenção — determinístico,
idempotente, auditável, com `--dry-run` e com falha física reportada.

## Deliverables

### R1 — Eventos auditáveis (D4)

- `CASE_RETAINED_DATA_PURGED` em `CaseEventType` (fase 1, atômico com a
  limpeza) + `CASE_RETAINED_DATA_PURGE_OUTCOME` (fase 2, desfecho físico
  condicional) + rótulos legíveis e badges em `apps/dashboard/event_labels.py`
  (o desfecho é CONSCIENTE DO PAYLOAD: `files_failed > 0` → badge de
  perigo — purga incompleta visível na trilha; anti-drift do mapa puxa o
  RED naturalmente).

### R2 — Serviço + comando (D4)

- **Serviço puro** `purge_closed_case_data(case, ...) -> resultado` em
  `apps/cases/closure.py`: reusa `_clean_acknowledged_clinical_data` +
  coleta pré-delete, em `transaction.atomic(durable=True)` (commit real
  obrigatório — recusa transação externa; a deleção física roda
  SINCRONAMENTE APÓS o atomic durável, mesma ordem banco→arquivos),
  registrando o evento de purga DENTRO do atomic e o evento de desfecho
  (tipo próprio, somente quando `files_attempted > 0`) após a deleção,
  com contagens `files_attempted/files_deleted/files_failed` para o
  comando reportar; payload SEM PHI (nomes de campos + contagens, nunca
  filenames).
- **Comando fino** `manage.py purge_closed_case_data [--dry-run]`: seleciona
  SOMENTE casos `CLEANED` com dados retidos (≥1 campo clínico/LLM
  não-vazio OU rows de documento/anexo); laço por caso (atomic por caso —
  falha no meio não deixa meia-purga); `--dry-run` lista referência +
  contagens sem alterar nada; saída com resumo (purgados/pulados) e, para
  casos com `files_failed > 0`, sinalização de purga INCOMPLETA com exit
  code não-zero listando os casos.

### R3 — Testes (RED→GREEN)

- Novos (RED primeiro): caso `CLEANED` retido é minimizado (campos vazios,
  rows/arquivos removidos, evento na trilha com payload de nomes de campos
  e contagens — sem filename no payload —, rótulo legível no mapa, serve →
  404); caso ativo com dados NÃO é tocado; caso `CLEANED` já minimizado é
  pulado; re-execução é no-op sem eventos duplicados (idempotência);
  `--dry-run` reporta e não altera; **falha física reportada**: storage
  cujo `delete` lança `OSError` → banco commitado mas caso reportado
  como incompleto com `files_failed > 0` e comando encerra com erro.
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/cases apps/dashboard`
  + suíte completa + gate completo do AGENTS.md.

## Gates para o reviewer (2 linhas)

1. A seleção é à prova de erro operacional: comando com caso ativo
   populado no fixture NÃO o altera (teste explícito), cada caso é um
   atomic DURÁVEL próprio (`durable=True` — recusa transação externa com
   erro nomeado; deleção física só após commit real) e o payload/saída/
   LOG não contêm filename nem conteúdo clínico.
2. A trilha prova a destruição E as falhas em duas fases: evento de purga
   ATÔMICO com a limpeza (sempre 1 por execução que purga — payload com
   nomes dos campos + rows + files_attempted) + evento complementar de
   desfecho físico quando houve arquivos (payload files_deleted/
   files_failed); re-execução não adiciona eventos (idempotência), e a
   falha física aparece no evento complementar, na saída e no exit code
   não-zero.

## Out of scope

- Agendamento automático/TTL da purga (operacional, por decisão do dono;
  ordem flip→dry-run→purga→registro vive no runbook do ADR-0010).
- Purga de casos ativos, de identificação do paciente ou de
  `anonymization_report` (fora da política).
- Notificações in-app sobre a purga (o evento na trilha é o registro).
