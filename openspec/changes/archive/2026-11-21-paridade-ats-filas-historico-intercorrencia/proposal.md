# Proposal

## Why

O HMD diverge do ats-web (projeto-fonte, decisão do dono: paridade essencial) em quatro
comportamentos operacionais: (1) o agendador vê apenas a primeira linha do sumário clínico
no detalhe do caso, enquanto o ats-web exibe o sumário completo; (2) casos antigos não são
localizáveis — não existe busca histórica por nome do paciente ou nº de ocorrência da
regulação, nem para o agendador nem para o NIR, e caso encerrado fora do escopo do criador
é irrecuperável; (3) a intercorrência pós-agendamento só existe antes da ciência do NIR
(`FINAL_REPLY_POSTED`), iniciada pelo agendador — no ats-web o NIR pode reabrir um caso já
encerrado (`CLEANED`) para re-agendamento; (4) as abas de trabalho são dirigidas por status
sem janela temporal, enquanto o ats-web usa abas «Hoje» (janela do dia corrente, escopo do
decisor) com o histórico coberto por superfícies de busca dedicadas.

A política de PHI do dono (registrada no design) estabelece que o único perímetro de
restrição relevante é a fronteira externa (anonimização de tudo que vai ao LLM, mantida);
visibilidade in-app entre papis do hospital não é eixo de restrição — os usuários já
acessam o prontuário eletrônico completo.

## What Changes

- **Sumário completo ao agendador**: o detalhe do caso para `scheduler`/`admin` passa a
  exibir o sumário clínico completo re-identificado (hoje: primeira linha). Demais
  artefatos do pipeline (estrutura extraída, alertas da policy, sugestão) seguem fora do
  escopo do agendador — paridade com o ats-web, que também não os exibe.
- **Busca de casos encerrados (NIR)**: nova página com busca server-side por nº de
  ocorrência OU nome do paciente sobre casos `CLEANED` e casos com intercorrência ativa,
  **de qualquer criador** (escopo histórico, guarda própria fail-closed — o escopo por
  criador de «meus casos» permanece intacto), cap de 50 resultados, referência humana
  (uid nunca em texto visível).
- **Detalhe histórico do NIR**: detalhe read-only de caso encerrado no escopo histórico,
  com formulário de abertura de intercorrência quando elegível.
- **Intercorrência pós-encerramento**: novo serviço de abertura pelo NIR em caso `CLEANED`
  com agendamento confirmado — transição FSM `CLEANED → AWAITING_SCHEDULING` (reentrada na
  fila de pendentes do agendador), motivo obrigatório, issue única ativa por caso, snapshot
  do agendamento anterior, evento de auditoria dedicado e notificações reutilizando os
  marcos existentes. Caso minimizado (flag ADR-0010) ou purgado é **inelegível**
  (fail-closed, com motivo exibido na busca). Ciclo completo: re-confirmação pelo
  agendador → nova resposta final → nova ciência → novo encerramento.
- **«Histórico» do agendador**: nova página com busca por nº de ocorrência OU nome sobre
  casos processados (confirmados/negados, incluindo `CLEANED`), cap de 50, com acesso de
  detalhe em escopo histórico.
- **Abas «Hoje»**: a aba «Decididos» do médico passa a «Decididos Hoje» (decisões do dia
  corrente, escopo do decisor) e a aba «Processados» do agendador passa a «Processados
  Hoje» (decisões de agendamento do dia corrente, escopo do agendador). Contadores,
  ordenação, partial de 20s e paginação preservados com a nova semântica. Casos antigos
  deixam as abas e passam a viver nas superfícies de histórico/busca — por isso as
  superfícies de arquivo são entregues ANTES do re-scope das filas.

## Capabilities

### New Capabilities

- `post-closure-issue`: Intercorrência pós-agendamento em caso encerrado — elegibilidade
  (caso `CLEANED`, agendamento confirmado, dados retidos, sem issue ativa), abertura pelo
  NIR com motivo obrigatório, reentrada no fluxo de agendamento, ciclo de re-encerramento
  e guarda de issue única.

### Modified Capabilities

- `scheduling`: «Visão do agendador limitada ao necessário» MODIFIED (sumário completo
  re-identificado no detalhe); «Fila do agendador completa» MODIFIED (aba «Processados
  Hoje» — janela do dia corrente por decisor); ADDED «Histórico do agendador» (busca por
  nº/nome sobre casos processados e encerrados com acesso de detalhe).
- `doctor-decision`: «Fila médica operacional com filtro por estado e subtipo» MODIFIED
  (aba «Decididos Hoje» — decisões do dia corrente, escopo do decisor; sem histórico
  médico — paridade ats-web, perda aprovada).
- `notifications`: «Notificações por marcos do caso» MODIFIED (reabertura por
  intercorrência passa a notificar também os agendadores ativos, além do criador).
- `intake-nir`: ADDED «Busca de casos encerrados e detalhe histórico» (busca por nº/nome,
  escopo não-criador fail-closed, cap 50, referência humana; abertura de intercorrência).

## Impact

- **apps/cases**: models (transição FSM `CLEANED → AWAITING_SCHEDULING`, campos compactos
  de issue ativa, marcador persistente de disposição de dados, carimbos de decisão —
  3 migrations aditivas com backfills auditáveis por eventos), catálogo de eventos (marco
  de reabertura pós-encerramento + fechamento de issue), interplay com `closure.py`
  (marcador gravado na minimização/purga; issue zerada em qualquer encerramento).
- **apps/accounts**: dispatcher de notificações (fan-out dos agendadores no marco de
  reabertura) + testes de destinatários/idempotência.
- **apps/intake**: views/urls/templates (busca de encerrados + detalhe histórico + POST de
  abertura), guards fail-closed com escopo histórico próprio.
- **apps/scheduler**: presenters (sumário completo), views/urls/templates (aba «Hoje» +
  página «Histórico»), serviços (abertura de issue reutilizando o padrão
  `reopen_scheduling_after_incident`).
- **apps/doctor**: views (re-scope da aba decididos — derivação de `doctor_decided_at` por
  caso), templates.
- **Specs**: `scheduling`, `doctor-decision`, `notifications` (deltas) + `intake-nir`
  (ADDED) + nova `post-closure-issue`.
- **Docs/contexto**: `PROJECT_CONTEXT.md` no arquivamento; manual do usuário (novas
  páginas e nova semântica de abas); emenda registrada ao ADR-0010 (reabertura x dados
  retidos).
- **Sem mudança**: anonimização/fronteira LLM (invariantes mantidos — tokens sempre,
  re-identificação só na renderização), escopo por criador de «meus casos», guards de
  papel/rota, dashboard, `pdf-original-viewer` (restrição de janela já é paridade),
  notificações (marcos existentes reutilizados).
