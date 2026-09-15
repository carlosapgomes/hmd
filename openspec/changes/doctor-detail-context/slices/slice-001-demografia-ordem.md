# Slice 001 — Demografia no card de identificação + ordem clínica dos cards

## Contexto necessário

- `apps/doctor/presenters.py` `build_case_detail_context` (~656): monta
  `identification = {"patient_name", "agency_record_number", "birth_date"}`
  a partir do `Case`.
- `templates/doctor/case_detail.html`: card de identificação (~L18-40) com
  `dl` de Paciente/Nº de ocorrência/Nascimento (`—` quando ausente);
  cards na ordem de produção: Procedimentos declarados (~L55) → Decisões
  registradas (~L78, condicional) → Alertas consultivos (~L118) →
  Recomendação agregada (~L192) → Requisitos gerais (~L214) → Prior-case
  (~L232) → Sumário clínico (~L268) → Estrutura extraída (~L284) → Trilha
  (~L308) → Documentos (~L341) → Anexos (~L380).
- Campos `Case.patient_age`/`patient_gender`/`patient_race` existem
  (change `sesab-header-extraction`, migration 0010).
- Testes existentes do detalhe: `apps/doctor/tests/test_detail.py` (+conftest).

## Goal

Card de identificação com demografia (idade/sexo/raça) e ordem de leitura
clínica (quadro clínico antes do consultivo), para caso em decisão e
decidido.

## Deliverables

### R1 — Presenter

- `identification` ganha `patient_age`, `patient_gender`, `patient_race`
  (direto dos campos; sem transformação).

### R2 — Template

- Card de identificação: linhas «Idade» (formato `84 a`), «Sexo» (`F`/`M`)
  e «Raça/Cor» (forma do enum), cada uma com `—` quando ausente — mesmo
  padrão dos campos existentes.
- MOVER os blocos «Sumário clínico» e «Estrutura extraída» para logo após
  «Procedimentos declarados» (posições relativas dos demais preservadas;
  nenhum conteúdo/conteúdo condicional alterado dentro dos blocos).

### R3 — Testes (RED→GREEN)

- Novos (RED): identification do presenter contém a demografia; template
  renderiza «84 a»/«F»/«Parda» no card de identificação; demografia
  ausente → três linhas com «—»; **ordem**: `response` do detalhe com
  índices crescentes de «Procedimentos declarados» < «Sumário clínico» <
  «Estrutura extraída» < «Alertas consultivos»; detalhe decidido read-only
  mantém a mesma ordem (mesmo template).
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/doctor` + suíte
  completa + ruff/format.

## Gates para o reviewer (2 linhas)

1. O movimento no template é PURAMENTE posicional: `git diff` mostra os
   dois blocos relocados byte-a-byte (nenhum conteúdo interno alterado) e
   nenhum outro bloco movido.
2. A ordem é pinnada por teste de ÍNDICES no HTML (find() crescente), não
   por presença de strings — falha se qualquer card voltar ao lugar antigo.

## Out of scope

- Filas (item 1 do backlog) e trilha de eventos (item 4) — changes próprios.
- Qualquer mudança em presenter de re-identificação de tokens, policy,
  alertas ou conteúdo dos cards.
