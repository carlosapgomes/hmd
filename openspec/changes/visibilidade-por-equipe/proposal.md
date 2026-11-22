# Proposal

## Why

O NIR é uma **equipe** que atua sobre todos os pacientes que passam pela regulação — não uma coleção de criadores individuais. O escopo por criador de «Meus Casos» (lista, detalhe ativo, visualização de PDF, ciência, gate e confirmação de procedimento — em views **e** serviços) já causou confusão operacional real em produção: após logar com conta diferente da que criou os casos (break-glass `admin`), a lista ficou vazia e o time interpretou os casos como "sumidos", enquanto a fila médica (global) os exibia normalmente.

O mesmo raciocínio vale para os outros dois papéis: médicos trabalham por especialidade e agendadores como equipe única. As abas «Decididos Hoje» e «Processados Hoje» hoje mostram apenas as decisões **do próprio usuário** (`doctor_decided_by=user` / `scheduling_decided_by=user`), escondendo o trabalho dos colegas exatamente nas superfícies de acompanhamento do dia.

## What Changes

- **NIR passa a enxergar a equipe (views e serviços)**: a lista «Meus Casos» passa a listar **todos os casos ativos (não-`CLEANED`) de qualquer criador**, renomeada para «Casos em Processamento» (nav, header, título, estado vazio, alerta pós-lote, breadcrumb e manual). O detalhe ativo e todas as ações de leitura/operação (PDF de relatório e anexos, ciência do NIR, liberação/reenvio do gate, confirmação de procedimento por divergência) ficam acessíveis a **qualquer `nir` ativo** — incluindo as guardas da camada de serviços (`_assert_owned_by` e o check de criador da ciência), substituídas por defesa em profundidade por papel dentro da transação.
- **«Decididos Hoje» vira da especialidade**: a aba (e o badge do header) mostra os casos decididos no dia corrente **de qualquer decisor** dentro do subtipo acessível ao médico que visualiza, com decisor visível no card. Rows de decisor nulo continuam excluídas (`__isnull=False` — contrato fail-closed das migrations 0013/0014).
- **«Processados Hoje» vira da equipe**: a aba (e o badge do header) mostra as decisões de agendamento do dia corrente **de qualquer decisor**, com decisor visível no card.
- **PDF/texto original do detalhe do agendador em escopo de equipe** (decisão do dono 2026-11-22): o gate de `Visualizar PDF Original`/`Texto Extraído do PDF` passa a ser **por decisão de agendamento + estado** (`scheduling_decided_at is not None and status in PDF_ALLOWED_STATUSES`), não por decisor — qualquer agendador vê e serve em casos que passaram pelo agendamento (confirmados ou negados pelo agendador); casos de negativa médica direta (sem decisão de agendamento) seguem sem acesso.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `intake-nir`: o requisito «Meus casos e detalhe do NIR» muda de escopo-por-criador para escopo-por-equipe (papel `nir` ativo) na lista, no detalhe ativo e nas ações derivadas (gate e confirmação de procedimento); renomeação do rótulo da superfície para «Casos em Processamento».
- `case-closure`: ciência do recebimento da resposta final e visualização do resultado passam a ser de **qualquer `nir`** (criador ou não), com o operador registrado como ator; links de «Reenvios corrigidos» deixam de particionar próprio/alheio.
- `doctor-decision`: o requisito da fila médica passa a definir «Decididos Hoje» como decisões do dia corrente **de qualquer decisor** dentro do acesso por subtipo do visualizador, com decisor visível no card.
- `scheduling`: «Processados Hoje» passa a decisões do dia corrente **de qualquer decisor**, com decisor visível no card; e os requisitos «Visão do agendador limitada ao necessário» **e** «Histórico do agendador» passam o gate de PDF/texto a **por decisão de agendamento + estado, para a equipe** (decisão do dono 2026-11-22).
- `pdf-original-viewer`: guards de serve/viewer — NIR passa a servir/ver qualquer caso (equipe); agendador gateado por decisão de agendamento + estado, não por decisor.
- `attachments`: listagem e serve de anexos de casos de **qualquer criador** para o papel `nir` (equipe).
- `ui-shell`: vocabulário de abas — «Meus Casos» → «Casos em Processamento» (desvio deliberado do ats-web, decisão do dono).

## Impact

- `apps/intake/views.py` — remover `created_by=user` de `_build_my_cases_context`, `case_detail`, `case_ack`, `pdf_viewer`, `attachment_pdf_viewer`, `serve_attachment`, `serve_document`, `gate_release`, `gate_resubmit`, `procedure_review_confirm` (2 queries); unificar redirect de recusa do `case_resubmit` (remove ramo `is_own`).
- `apps/intake/services.py` — remover `_assert_owned_by` de `release_retained_case`, `confirm_case_procedure_review`, `resubmit_case_documents`; re-check de papel `nir` na transação (defesa em profundidade, padrão já existente).
- `apps/cases/closure.py` — remover check de criador em `acknowledge_case_receipt` (operador segue como ator) + re-check de papel `nir` na transação.
- `apps/intake/views.py` (gate/ciência) — tradução `PermissionError → PermissionDenied` (403) em `gate_release`, `gate_resubmit` e `case_ack`, espelhando `procedure_review_confirm`.
- `apps/accounts/context_processors.py` — `queue_counters`: `*_decided_by=user` → `*_decided_by__isnull=False` (doctor mantém matriz de subtipo).
- `apps/doctor/views.py` — aba «Decididos Hoje»: `doctor_decided_by__isnull=False` + `select_related`; decisor no card.
- `apps/scheduler/views.py` — aba «Processados Hoje»: `scheduling_decided_by__isnull=False` + `select_related`, decisor no card; `can_view_pdf` perde `scheduled_by == user` e ganha guarda de decisão: `scheduling_decided_at is not None and status in PDF_ALLOWED_STATUSES` (confirmação E negação de agendamento; negativa médica direta continua 404) — inclui `historical_case_detail`.
- Templates: nav/header/título/estado vazio da lista, `home.html` (alerta pós-lote), `case_detail.html` (breadcrumb), `_my_cases_content.html` (`<h2>`), cards das abas "hoje" (decisor), manual (seções NIR, Médico e Agendador).
- Suíte de testes: inversão de cenários de escopo (D6) + testes diretos de serviço para NIR não-criador + `test_queue_counters` atualizado.
- **Fora de escopo**: fan-out de notificações (permanece criador; backlog), busca de encerrados (já global), abas Pendentes (já globais), FSM e fluxo assíncrono.
