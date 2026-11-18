# Tasks: retencao-auditoria-casos-encerrados

## 1. Slice 001 — Modo de retenção no encerramento (flag + gate + worker de anexos + textos + ADR)

- [x] 1.1 RED: novos testes de retenção (ciência e encerramento administrativo
      preservam os 7 campos, rows de documentos/anexos e arquivos físicos;
      FSM/eventos/lock/notificação inalterados; serve de doc retido → 200 no
      intake-criador E no painel-manager), corrida worker de anexos ×
      encerramento administrativo (no-op SEM dispatch de OCR externo) e textos
      de UI/rótulo por modo + pinos
      `override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True)` nas suítes
      que assertam minimização/404 hoje — `uv run pytest apps/cases
      apps/intake apps/dashboard apps/attachments` falha apenas nos novos
      (`test_header_persistence.py` segue verde sem pino)
- [x] 1.2 GREEN: `CASE_CLOSURE_MINIMIZATION_ENABLED` (default `false`) em
      `config/settings/base.py` + `.env.example` + passthrough no serviço
      `web` do `docker-compose.prod.yml`; gate NOS entry points de
      `apps/cases/closure.py` (função interna `_clean_...` incondicional);
      gate `CLEANED → no-op` no worker de anexos antes de cada etapa (incl.
      dispatch externo); textos de ciência/encerramento/manual condicionais
      ao modo + rótulo `CASE_STATUS_CLEANED` renomeado — bateria do slice
      verde
- [x] 1.3 ADR-0010 (política temporária — respostas do dono 2026-11-17: base
      jurídica = execução íntegra e exclusiva no perímetro do hospital com
      autorização expressa dos gestores, cobrindo auditoria e benchmark;
      gatilho de reversão = produto alcançar a versão 1.0; responsável =
      gerência de TI do hospital; mais escopo e runbook
      flip→dry-run→purga→registro de execução) + gate completo do AGENTS.md
      (ruff + format + mypy + pytest + manage.py check)

## 2. Slice 002 — Card «Sumário apresentado ao médico» no painel

- [x] 2.1 RED: testes HTTP do card (manager vê sumário clínico + estrutura
      extraída re-identificados e sem tokens; caso `CLEANED` retido renderiza
      conteúdo; sem artefatos → placeholders POR BLOCO; papéis fora de
      manager/admin → 403; paridade de `summary_lines`/`sections` com o
      contexto médico)
- [x] 2.2 GREEN: builders puros + mapas dependentes extraídos para
      `apps/cases/presentation.py` com API pública única
      `build_structure_sections(structured)` (presenter médico importa —
      extração pura, suíte médica run-only) +
      `apps/dashboard/presenters.py::build_case_summary_context` + card
      collapsible fechado em `templates/dashboard/case_detail.html` (após
      «Procedimentos declarados», antes de «Texto Extraído do PDF»)
- [x] 2.3 Bateria do slice + regressão do detalhe médico sem edição de
      testes existentes (`pytest apps/doctor apps/dashboard`) + gate completo

## 3. Slice 003 — Purga retroativa dos dados retidos

- [x] 3.1 RED: testes do comando (minimiza `CLEANED` com dados retidos;
      casos ativos intocados; idempotente sem eventos duplicados; `--dry-run`
      reporta sem alterar; evento `CASE_RETAINED_DATA_PURGED` por caso com
      payload de NOMES de campos + contagens — sem filename — e rótulo
      legível; falha física de arquivo → caso reportado incompleto + exit
      code não-zero)
- [x] 3.2 GREEN: `CASE_RETAINED_DATA_PURGED` no enum `CaseEventType` +
      rótulo em `apps/dashboard/event_labels.py` + serviço
      `purge_closed_case_data` em `apps/cases/closure.py` (reusa
      `_clean_acknowledged_clinical_data`, atomic por caso, contagens
      `files_attempted/files_deleted/files_failed`) + comando fino
      `manage.py purge_closed_case_data [--dry-run]`
- [x] 3.3 Bateria do slice + gate completo

## 4. Encerramento

- [ ] 4.1 E2E no dev seguindo o runbook do ADR: encerrar caso por ciência e
      por admin com retenção e conferir preservação + card no painel; flip
      do flag → `--dry-run` da purga → purga real (campos zerados, docs →
      404, evento na trilha) → conferir card do painel com placeholders
      pós-purga; registro da execução no runbook
- [ ] 4.2 Archive do change (sync specs main + PROJECT_CONTEXT.md — ADR-0010
      já contém as respostas do dono)
