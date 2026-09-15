# Proposal: doctor-detail-context

## Why

O dono validou o E2E em dev e listou (backlog itens 2-3): (a) o detalhe do
caso médico deve exibir a identificação completa do paciente — o card
«Identificação do paciente» existe e renderiza nome/nº de ocorrência
(linkage agora populado pelo cabeçalho SESAB), mas não mostra a demografia
(idade/sexo/raça) que o change `sesab-header-extraction` passou a extrair;
(b) a ordem dos cards segue a ordem de construção do pipeline, não a ordem
de leitura clínica — o médico quer o quadro clínico primeiro
(«Procedimentos declarados» → «Sumário clínico» → «Estrutura extraída») e
só depois o consultivo da automação («Alertas consultivos», recomendação
agregada, requisitos gerais, prior-case).

## What Changes

- **Card de identificação** ganha a demografia do caso: Idade, Sexo e
  Raça/Cor (campos `patient_age`/`patient_gender`/`patient_race` do
  cabeçalho SESAB), exibidos quando presentes com `—` quando ausentes —
  mesmo padrão dos campos existentes; re-identificação restrita à
  renderização para `doctor`/`admin` (demografia já é campo estrutural do
  caso, sem passagem por LLM).
- **Ordem de leitura clínica** no detalhe (mesmo template para caso em
  decisão e decidido read-only): Identificação → Procedimentos declarados
  → **Sumário clínico** → **Estrutura extraída** → Decisões registradas →
  Alertas consultivos → Recomendação agregada → Requisitos gerais →
  Prior-case → Trilha → Documentos → Anexos. A mudança é mover os dois
  cards do quadro clínico para logo após «Procedimentos declarados»
  (posições relativas dos demais preservadas).

## Impact

- **Spec**: `doctor-decision` (MODIFIED «Presenter re-identificado sob
  papel autorizado»: demografia na identificação + ordem clínica).
- **Código**: `apps/doctor/presenters.py` (dict `identification` + 3
  campos), `templates/doctor/case_detail.html` (linhas do card + ordem dos
  blocos). Sem modelo/migration/worker/UI de outras superfícies.
- **Backlog**: fecha os itens 2-3; desbloqueia o item 1 (filas) que segue
  como próximo change.
