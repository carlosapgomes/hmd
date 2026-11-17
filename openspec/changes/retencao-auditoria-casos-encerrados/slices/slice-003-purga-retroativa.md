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

### R1 — Evento auditável (D4)

- `CASE_RETAINED_DATA_PURGED` em `CaseEventType` + rótulo legível e badge
  em `apps/dashboard/event_labels.py` (trilha do painel exibe a purga;
  anti-drift do mapa puxa o RED naturalmente).

### R2 — Serviço + comando (D4)

- **Serviço puro** `purge_closed_case_data(case, ...) -> resultado` em
  `apps/cases/closure.py`: reusa `_clean_acknowledged_clinical_data` +
  coleta pré-delete + deleção física em `on_commit`, no MESMO atomic por
  caso do closure; registra `CASE_RETAINED_DATA_PURGED` (ator "Sistema",
  payload = NOMES dos 7 campos zerados + CONTAGENS de rows/arquivos —
  nunca conteúdo clínico nem nomes de arquivo); devolve contagens
  `files_attempted/files_deleted/files_failed` para o comando reportar.
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
   atomic próprio (falha no meio não deixa meia-purga) e o payload/saída
   não contêm filename nem conteúdo clínico.
2. A trilha prova a destruição E as falhas: cada caso purgado tem
   exatamente UM `CASE_RETAINED_DATA_PURGED` (re-execução não duplica),
   com rótulo legível, e caso com falha física aparece como incompleto na
   saída com exit code não-zero.

## Out of scope

- Agendamento automático/TTL da purga (operacional, por decisão do dono;
  ordem flip→dry-run→purga→registro vive no runbook do ADR-0010).
- Purga de casos ativos, de identificação do paciente ou de
  `anonymization_report` (fora da política).
- Notificações in-app sobre a purga (o evento na trilha é o registro).
