# Proposal

## Why

A trilha de eventos (`CaseEvent`, append-only, com ator/papel/timestamp) é a fonte
de verdade da história do caso, mas hoje só é visível no detalhe do painel
(manager/admin). O NIR vê decisões e agendamento sem saber **quem** agiu (o
resultado do detalhe expõe o quê e quando, nunca o ator); o médico vê apenas o
ator do próprio ato («Decisões registradas»); o agendador vê `decided_by` do
agendamento e autores de comunicações, mas não a decisão clínica nem a criação.
A decisão de 2026-09-15 (`painel-ats-parity` D4) removeu a trilha do NIR/médico
por jargão técnico — desde então a trilha ganhou rótulos legíveis completos (45/45
tipos, teste anti-drift), badges coloridos por papel, papéis em português
(`role_label`) e linhas de detalhe com conteúdo, o que dissolve o motivo original
da remoção. O dono decide agora pela paridade: a mesma trilha do supervisor passa
a ficar disponível para todos os perfis.

## What Changes

- Novo módulo `apps/cases/event_trail.py` (casa neutra do domínio — mesmo
  padrão de `event_detail_lines` em `apps/cases/procedure_review.py`):
  `EVENT_LABELS`/`EVENT_BADGE_CSS`/`event_label`/`event_badge_css` **migram** de
  `apps/dashboard/event_labels.py` (dashboard passa a importar de `cases`;
  dependência unidirecional preservada) + novo `build_event_trail_context(case)`
  (serviço puro, `select_related("actor")`, sem N+1).
- Novo partial compartilhado `templates/cases/_event_trail.html` com o card
  collapsible «Trilha de eventos» **fechado por padrão** — markup idêntico ao do
  painel; o detalhe do painel passa a incluí-lo (paridade de renderização por
  construção).
- O detalhe do caso dos papéis **nir**, **doctor** e **scheduler** monta
  `event_trail` e inclui o partial **no final da página** (após o último card
  existente): NIR vê quem decidiu/agendou/encerrou; médico vê criação e
  pós-decisão; agendador vê decisão clínica e criação. Trilha idêntica em todas
  as superfícies (sem filtro por papel — paridade de auditoria).
- Sem rotas, modelos, guards ou migrações novos — a trilha viaja nos guards
  existentes de cada detalhe (queryset `created_by` do NIR, matriz de subtipo do
  médico, `role_required` do agendador).
- Badge de erro «Falha no processamento» do NIR **mantido**; o motivo técnico
  continua fora da renderização (o partial só projeta `event_detail_lines`,
  que não expõe payload de falha).
- Specs MODIFIED: `intake-nir` (detalhe passa a exibir a trilha), `doctor-decision`
  (detalhe em decisão e detalhe decidido passam a exibir), `scheduling` (visão do
  agendador ganha a trilha). `dashboard` inalterada em comportamento (mesma
  trilha, agora via partial compartilhado); cenário «Essenciais preservados após
  a limpeza» de `case-closure` (trilha «continua presente») deixa de ser
  ambíguo — nenhum delta necessário lá.

## Capabilities

### New Capabilities

(nenhuma — reuso do comportamento existente da trilha em novas superfícies)

### Modified Capabilities

- `intake-nir`: requisito «Meus casos e detalhe do NIR» — o detalhe passa a
  exibir a trilha de eventos em card collapsible no final; cenários «Detalhe
  exibe documentos e trilha» e «Caso FAILED exibe badge de erro no detalhe do
  NIR» atualizados (badge mantido, motivo técnico segue oculto).
- `doctor-decision`: requisitos «Presenter re-identificado sob papel autorizado»
  e «Caso decidido consultável» — o detalhe passa a exibir a trilha; cenários
  «Detalhe em decisão sem trilha de eventos» e «Detalhe decidido read-only» com
  corpos atualizados (títulos verbatim preservados — quirk documentado do
  formato em `painel-ats-parity` D5b).
- `scheduling`: requisito «Visão do agendador limitada ao necessário» — a visão
  ganha o card da trilha ao final, sem ampliar o acesso a artefatos do pipeline
  (sumário/estrutura/alertas seguem fora do alcance do agendador).
- `account-access`: requisito «Rótulos de papel em português na interface» —
  superfícies da trilha estendidas a todos os detalhes (a spec dizia «os
  detalhes de NIR e médico não exibem mais trilha»; cenários de rótulo na
  trilha saem da restrição ao painel).

## Impact

- **Código**: `apps/cases/event_trail.py` (novo, migração de
  `apps/dashboard/event_labels.py`), `apps/dashboard/views.py` (import + include
  do partial), `apps/dashboard/event_labels.py` removido, `apps/intake/views.py`,
  `apps/doctor/presenters.py` (ou `views.py`), `apps/scheduler/presenters.py`,
  `templates/cases/_event_trail.html` (novo), `templates/{dashboard,intake,doctor,
  scheduler}/case_detail.html`.
- **Testes**: `apps/dashboard/tests/test_event_labels.py` migra/importa do novo
  caminho; asserts de ausência viram asserts de presença em
  `apps/intake/tests/test_my_cases.py`, `apps/doctor/tests/test_detail.py`,
  `apps/doctor/tests/test_decision.py`, `apps/accounts/tests/test_role_labels.py`
  (+ ajustes de import em `apps/dashboard/tests/test_views.py` e
  `apps/cases/tests/test_purge_closed_case_data.py`, que importam o módulo de
  labels migrado — sem editar asserções);
  cobertura nova para scheduler; testes de renderização do dashboard seguem
  válidos (markup preservado via partial).
- **Sem impacto**: modelos/migrações, rotas/urls, guards, workers, pipeline LLM,
  anonimização, notificações, PWA.
- **Custo por view**: +1 query (`events.select_related("actor")`) por
  renderização de detalhe — desprezível.
- **Decisão revertida**: registra a revisão da decisão do dono de 2026-09-15
  (backlog pós-piloto, item 4 / `painel-ats-parity` D4) — motivação no Why;
  inclui a frase da spec `account-access` que dizia «a trilha vive no painel».
