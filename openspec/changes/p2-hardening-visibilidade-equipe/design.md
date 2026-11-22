# Design — p2-hardening-visibilidade-equipe

## Context

Quitância dos P2s/notas deferidos pela execução da change `visibilidade-por-equipe`
(checkpoints `vis-slice-00{3,5,6,7}-r1`, disponíveis em
`/tmp/hmd-slice-runs/hmd-case-lifecycle/visibilidade-por-equipe/`). Itens e origem:

1. (S5/review-1) Parâmetro `user` sem uso em `_decided_today_cases` (`apps/doctor/views.py:225-230`) após a troca para `doctor_decided_by__isnull=False`.
2. (S6/review-1) Teste invertido cobre confirmação E negação alheia na página cheia, mas só confirmação no partial htmx (`apps/scheduler/tests/test_processed_today.py:185-199`).
3. (S7/review-1) Manual do agendador afirma que negativa médica direta não expõe PDF/texto «a ninguém» (`templates/accounts/manual.html:270-271`) — inexato: NIR serve sem guarda de decisão (design D1/D7 da change original); e a redação não menciona o gate de estados.
4. (S7/worker) `AWAITING_NIR_ACK` (membro de `PDF_ALLOWED_STATUSES`) sem teste dedicado do gate `_scheduler_can_view_pdf`.
5. (S3/review-1) Re-check de papel inline em `confirm_case_procedure_review` duplica o helper `_assert_nir_role` (convergência avaliada como opcional).

## Goals / Non-Goals

**Goals**: quitar os 5 itens com a menor mudança correta; suíte segue 100% verde; zero mudança de comportamento observável (exceto mensagens de erro de `PermissionError` que permanecem SEMANTICAMENTE idênticas por ação).

**Non-Goals**: notificações por equipe (decisão de produto pendente do dono — backlog); qualquer delta de spec (a spec `pwa-manual` descreve o manual genericamente e não pinna o texto); refactors além dos nomeados.

## Decisions

- **D1 — Helper com mensagem por ação**: `_assert_nir_role(user, role, *, action)` ganha parâmetro de contexto para preservar as mensagens específicas («Ação de gate restrita ao papel nir.» vs «Confirmação de procedimento restrita ao papel nir.»); os 3 call sites atuais e o re-check inline convergem para o helper. Testes que asserem as mensagens permanecem verdes sem edição (paridade de string).
- **D2 — Fixture AWAITING_NIR_ACK via caminho real**: o teste dedicado conduz um caso de decisão de agendamento até `AWAITING_NIR_ACK` pelo fluxo real (sem `update()` cru que falsificasse transições) e cobre os dois lados do gate (com decisão → visível; negativa médica no mesmo estado, sem decisão → 404/sem cards, se o estado for alcançável pelo fluxo; senão, documento a inalcançabilidade no relatório e cobre apenas o lado positivo).
- **D3 — Manual precisa, não expansionista**: corrigir apenas as duas frases (escopo da restrição = equipe de agendamento; menção de que o gate vale nos estados permitidos — reaberto/encerrado fora). Não reescrever a seção.

## Risks / Trade-offs

- Risco mínimo: itens 1 e 5 são refactors de higiene com paridade de comportamento exigida por teste; itens 2-4 são adições de teste/doc.
- Suíte atual: 2401 passed — meta: verde contínuo (nenhum teste existente alterado, salvo marcadores do manual se o texto citado mudar).

## Migration Plan

Nenhuma migração. Dois slices sequenciais; cada um deixa o repo coeso.

## Open Questions

Nenhuma bloqueante.
