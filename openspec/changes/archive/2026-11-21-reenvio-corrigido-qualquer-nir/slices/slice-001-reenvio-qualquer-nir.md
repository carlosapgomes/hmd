# Slice 001 — Reenvio corrigido por qualquer NIR

## 1. Identity

- Slice: `slice-001-reenvio-qualquer-nir`
- Change: `reenvio-corrigido-qualquer-nir`
- Task: `tasks.md` § 1

## 2. Objective

Qualquer usuário com papel ativo `nir` (criador ou não) passa a poder reenviar de
forma corrigida um caso `CLEANED`, com entrada «Reenviar corrigido» no detalhe
histórico da busca; o reenviador é o criador do novo caso.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/reenvio-corrigido-qualquer-nir/proposal.md` e `design.md` (D1/D2/D4)
- Deltas: `specs/case-closure/spec.md` (reenvio MODIFIED) e `specs/intake-nir/spec.md` (busca MODIFIED)
- `apps/intake/views.py` — `corrected_resubmission` (GET/POST, guarda criador-scoped ~660-700), `closed_case_detail` (detalhe histórico), `case_detail` (`can_resubpatir`... `can_resubmit` ~588)
- `apps/intake/services.py` — `create_corrected_resubmission` (remover APENAS `_assert_owned_by` + docstring; validações 1 PDF/1 tipo/motivo/estado/supersedição intactas — review BLOCKER)
- `apps/intake/forms.py`, `templates/intake/corrected_resubmission.html`, `templates/intake/closed_case_detail.html`
- Paridade: `/projects/dev/ats-web/apps/intake/views.py:1374` (`corrected_resubmission` sem filtro por criador)

## 4. Requirements

- R1: guarda relaxada — `GET/POST` de reenvio acessível a papel ativo `nir`/`admin`
  para caso `CLEANED` **de qualquer criador**; caso fora de `CLEANED` → recusa
  nomeada sem escrita (comportamento vigente preservado); papel errado → 403.
- R2: ação «Reenviar corrigido» no **detalhe histórico** (`closed_case_detail`),
  gated por `status == CLEANED` (não por posse), linkando à página de reenvio
  existente; o botão no detalhe encerrado do criador permanece funcionando.
- R3: reenviador = criador do novo caso (`created_by`/`correction_created_by`);
  eventos de correção/supersedição gravados com o reenviador como autor; validações
  de 1 PDF/1 tipo/motivo e mensagens com referência humana/desambiguação seguem
  idênticas para criador e não-criador.
- R4: navegação da página de reenvio para o não-criador sem 404 (breadcrumb,
  «Cancelar» e redirects de recusa → detalhe histórico do original; sucesso →
  detalhe do novo caso do reenviador) — review MAJOR.
- R5: links condicionais em «Reenvios corrigidos deste caso» (próprio → detalhe;
  alheio ativo → referência sem link; alheio encerrado → detalhe histórico) —
  review MAJOR.
- Evidência: testes cobrindo R1–R5 (incl. POST válido por não-criador criando o caso
  vinculado; POST inválido sem escrita; recusa fora de `CLEANED`; teste DIRETO do
  serviço com não-criador).

## 5. Expected blast radius

- Esperado: `apps/intake/views.py` (guarda + contexto do detalhe histórico),
  `apps/intake/services.py` (só a remoção do `_assert_owned_by` + docstring),
  `templates/intake/closed_case_detail.html` (ação),
  `templates/intake/corrected_resubmission.html` (breadcrumb/cancelar → detalhe
  histórico), `templates/intake/case_detail.html` (links condicionais dos reenvios),
  testes do intake.
- Sensível (não mudar sem escalada): validações do serviço (estado/upload/motivo),
  guards por criador do `case_detail` ATIVO (exceto os links condicionais, que
  RESPEITAM o guard), fluxo de intercorrência.
- Comportamento preservado: ciência apenas do criador; reenvio com múltiplos
  arquivos/tipos rejeitado; original íntegro.

## 6. Failing-before plan

- Targeted tests: (a) NIR não-criador completa reenvio válido de caso `CLEANED`
  alheio (hoje: recusa/redirect — falha; inclui chamada DIRETA ao serviço, hoje
  `Http404` do `_assert_owned_by`); (b) detalhe histórico de caso `CLEANED` exibe
  ação «Reenviar corrigido» e o de não-`CLEANED` não exibe (hoje: ausente — falha);
  (c) reenviador = `created_by` do novo caso; (d) breadcrumb/cancelar/recusa sem 404
  para não-criador (hoje: `case_detail` criador-scoped — falha); (e) link do caso
  corrigido alheio condicionado (hoje: link incondicional — falha).
- Expected failure: registrar saída antes de implementar.

## 7. Implementation constraints

- Sem migration; no serviço, remover APENAS a guarda de posse (docstring junto);
  sem afrouxar outros guards (os links condicionais respeitam o guard ativo).
- Ação do detalhe histórico usa a MESMA rota/página de reenvio (sem segunda view).
- Manual do usuário só no slice 002.

## 8. Validation

- Focused: `uv run pytest apps/intake -k "resubmit or closed"` (nomes conforme suíte).
- Project gates: bateria completa de `AGENTS.md` §2.
- Runtime: não exigida.

## 9. Acceptance criteria

- [ ] R1: não-criador reenvia com sucesso; fora de `CLEANED` recusado sem escrita.
- [ ] R2: ação presente no detalhe histórico para `CLEANED` (ausente para outros).
- [ ] R3: novo caso criado pelo reenviador, vinculado, com eventos de supersedição.
- [ ] Validações de upload/motivo intactas para todos.
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: o relaxamento da guarda revelar dependência
não mapeada em notificações/eventos do novo caso; se o detalhe histórico exigir
mudança nos gates de PDF/anexos; se surgir decisão de produto (ex.: limitar reenvio
de caso com dados dispostos — hoje permitido, pois o novo ciclo parte de documentos
novos).

## 11. Evidence report

`/tmp/hmd-reenvio-001-report.md` (estrutura canônica).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
