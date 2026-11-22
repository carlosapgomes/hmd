# Proposal

## Why

O subtipo de médico `neuro` foi especificado no plano inicial como cobrindo "exames de neuroradiologia/**neurointervenção**", mas o catálogo de 13 tipos materializou apenas um tipo neuro — `art_cerebral`, diagnóstico. Pedidos de **embolização de aneurisma ou malformação vascular intracraniana** não podem nem ser declarados: o catálogo é fail-fast e rejeita tipos desconhecidos. O termo "procedimentos da neurointervenção" consta do documento clínico (`temp/parametrosHMD.md`, lista geral de suporte anestésico), mas nunca virou tipo com critérios próprios — a lacuna foi confirmada em auditoria dos changes arquivados (nenhuma decisão sobre o tipo; a menção de 2026-09-07 no slice-001 R5 tratou-a como categoria anestésica absorvida por `art_cerebral`).

Decisão do dono (2026-11-22): a neurointervenção **segue os requisitos clínicos da seção S2** (angioplastias de carótidas/membros/coronariana) — a seção mais restritiva já delimitada — sem criar seção nova.

## What Changes

- Novo tipo de procedimento no catálogo code-first: `neurointerv` (Neurointervenção (embolização de aneurisma ou malformação vascular intracraniana)), seção de critérios **S2**, subtipo doctor **neuro**, suporte anestésico **True**.
- `EXPECTED_PROCEDURE_COUNT` 13 → 14; `CRITERIA_SECTIONS["S2"].procedure_types` ganha o novo tipo (nenhuma seção nova, nenhum threshold novo).
- Seeds de prompt por tipo: +2 templates (`proc.neurointerv.llm1.user` / `proc.neurointerv.llm2.user`) com frase de contexto clínica; help do comando `seed_prompts` atualizado (29→31); sem bloco específico de schema (só a base comum, pela regra "nada fora da fonte").
- Texto visível ao usuário no card de revisão de procedimento (`templates/intake/case_detail.html`) alinhado à spec: "um dos 14 tipos do catálogo" (hoje fixa "13 tipos").
- Fixações mecânicas de contagem/lista atualizadas: testes do catálogo (13→14), seeds de prompt (29→31, incluindo `apps/accounts`), enum de detecção do LLM1 (`apps/pipeline/tests/test_schemas.py`) e `CATALOG_SIZE` do select de revisão (`apps/intake/tests/test_procedure_review_ui.py`); pinagens novas de roteamento (fila médica), detecção e selects.
- Sem migração de dados, sem novo subtipo de médico, sem mudança de policy (S2 já é consumida por mapeamento de seção).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `case-management`: requisito "Catálogo de 13 tipos de procedimento" — passa a 14 tipos com a neurointervenção (renomeado para não fixar contagem no nome); cenário de coerência atualizado.
- `intake-nir`: requisito "Confirmação explícita do procedimento pelo NIR" — a escolha passa a ser "um dos 14 tipos do catálogo" (texto fixava "os 13 tipos").

## Impact

- **Código (produção)**: `apps/cases/procedure_catalog.py` (perfil, contagem, S2), `apps/llm/prompts_seed.py` (contexto + seeds ×14), `apps/llm/management/commands/seed_prompts.py` (help/docstring 29→31), `templates/intake/case_detail.html` (apenas texto visível do card de revisão — sem lógica).
- **Testes**: `apps/cases/tests/test_procedure_catalog.py`, `apps/llm/tests/test_prompts.py`, `apps/accounts/tests/test_seed_procedure_review.py` (fixações 13/29), `apps/pipeline/tests/test_schemas.py` (enum 13→14 + detecção do novo tipo sem bloco), `apps/intake/tests/test_procedure_review_ui.py` (`CATALOG_SIZE`, rename do teste de 13 opções, texto do card) e `apps/intake/tests/test_procedure_combobox.py` (pinagem do novo tipo), `apps/doctor/tests/test_queue.py` (pinagens de roteamento).
- **Derivações automáticas** (sem tocar código): choices do select de intake, roteamento da fila médica (`_TYPES_BY_DOCTOR_SUBTYPE`), métricas do dashboard, policy S2.
- **Documentação em código e docs** (varredura do slice 002): comentários/docstrings com "13 tipos", "10 tipos", "(13)", "× 13", "26 user", "28/29 templates" em `apps/pipeline/schemas/`, `apps/intake/`, `config/settings/base.py`, `static/`, `templates/intake/my_cases.html`, `README.md` e suítes de teste; ajuste do runbook de produção (passo de seeds obrigatório mesmo em release "sem migrations").
- **Operação**: `seed_prompts` (idempotente) cria os 2 templates novos — em produção roda no passo 5 do runbook (`--profile migrate run --rm migrate`), **obrigatório nesta release mesmo sem migrations**; `seed_procedure_catalog` volta a validar 14/14.
- **Sem impacto**: subtipos de médico (`DoctorSpecialty` já semeia `neuro`), migrações, FSM, schemas Pydantic (nenhum bloco novo).
