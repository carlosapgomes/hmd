# Slice 003 — Busca NIR de encerrados e abertura de intercorrência

## 1. Identity

- Slice: `slice-003-busca-nir-encerrados`
- Change: `paridade-ats-filas-historico-intercorrencia`
- Task: `tasks.md` § 3 — depende do slice 002 (serviço de abertura).

## 2. Objective

Superfície NIR para localizar casos encerrados (qualquer criador) por nº de ocorrência ou
nome do paciente, consultar o detalhe histórico read-only e abrir a intercorrência
pós-encerramento em casos elegíveis.

## 3. Read first

- `AGENTS.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/proposal.md`
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/design.md` — D1/D6
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/intake-nir/spec.md` — ADDED «Busca de casos encerrados e detalhe histórico»
- `openspec/changes/paridade-ats-filas-historico-intercorrencia/specs/post-closure-issue/spec.md` — R1 (elegibilidade exibida) e R2 (POST)
- `apps/intake/views.py` — padrões de «meus casos» (busca `q` mín. 3 chars), guards fail-closed por criador (que NÃO mudam), `_administrative_closure_result`
- `apps/intake/urls.py`, `apps/intake/forms.py` (padrão de forms com motivo obrigatório)
- `templates/intake/` — padrões de lista/detalhe e referência humana
- serviço e elegibilidade do slice 002 (`apps/cases/closure.py`)
- `/projects/dev/ats-web/apps/intake/views.py` — `closed_cases_search`/`closed_case_detail` (referência de paridade)

## 4. Requirements

- R1: página de busca (papel ativo `nir`) — universo `Q(CLEANED) | Q(issue ativa)`,
  qualquer criador; termo casa `agency_record_number__icontains` OU
  `patient_name__icontains`; cap 50 por consulta (`order_by("-created_at")` antes do
  slice); estado vazio sem critério; referência humana com sufixo curto quando o nº está
  duplicado; elegibilidade exibida por resultado (ação/badge) com motivo quando inelegível.
- R2: detalhe histórico — read-only com identificação, resultado, comunicações e trilha
  de eventos fechada por padrão; sem ações de edição; form de intercorrência apenas para
  elegíveis; acesso direto por URL a caso fora do escopo histórico → 404.
- R3: POST de abertura — usa o serviço do slice 002; sucesso → flash + redirect ao
  detalhe; falha de validação → re-render com erro, sem escrita (garantia do serviço).
- R4: guards — papel não-NIR → 403; rotas cobertas pelo guard de intranet do papel `nir`
  (ADR-0002); o escopo por criador das rotas vigentes de «meus casos» permanece intocado.
- Evidência: testes de view/template em `apps/intake/tests/` cobrindo R1–R4.

## 5. Expected blast radius

- Esperado: `apps/intake/views.py`, `apps/intake/urls.py`, `apps/intake/forms.py` (form
  novo), `templates/intake/closed_cases_search.html` + `closed_case_detail.html` (nomes
  finais conforme convenção), testes.
- Sensível (não mudar sem escalada): guards criador das views vigentes, fluxo de
  reenvio corrigido, `serve_document`/`serve_attachment`, aba «Encerrados» de «meus
  casos» (permanece escopo criador).
- Comportamento anterior preservado: «meus casos» e detalhe do criador exatamente como
  estão.

## 6. Failing-before plan

- Targeted tests: 7 cenários do delta `intake-nir` (busca por nome de outro criador,
  busca por nº com issue ativa, casos em andamento fora, estado vazio, detalhe read-only
  com trilha, 403 não-NIR, 404 fora do escopo histórico) + POST válido/inválido.
- Expected failure: 404 de rota (views não existem); registrar antes de implementar.

## 7. Implementation constraints

- Nenhuma escrita fora do POST de intercorrência (busca e detalhe são leitura).
- Referência humana em toda textualidade (uid só como chave de URL).
- Não implementar histórico do agendador (slice 004) nem abas «Hoje» (slice 005).
- Badge/ação de elegibilidade derivam do serviço do slice 002 — sem duplicar regra.

## 8. Validation

- Focused: `uv run pytest apps/intake -k "closed"` (nomes conforme suíte).
- Project gates: bateria completa de `AGENTS.md` § 2.
- Runtime: smoke manual opcional (`runserver` dev + busca por nome/nº com dados seed),
  evidência registrada se executado.

## 9. Acceptance criteria

- [ ] R1: busca por nome/nº cobre encerrados + issues ativas de qualquer criador, cap 50,
      estado vazio, referência humana, elegibilidade por resultado.
- [ ] R2: detalhe histórico read-only com trilha; 404 fora do escopo.
- [ ] R3: POST válido reabre (caso em `AWAITING_SCHEDULING`) e inválido não escreve.
- [ ] R4: 403 não-NIR; intranet guard cobre as rotas novas; guards criador intactos.
- [ ] Quality gate completo verde.

## 10. Escalation conditions

Parar com `BLOCKED_NEEDS_DECISION` se: a UI revelar necessidade de mudar o serviço do
slice 002; se o guard de intranet não se aplicar às rotas novas por configuração; se
surgir decisão de produto sobre o que o detalhe histórico exibe (conteúdo clínico além do
resultado).

## 11. Evidence report

`/tmp/hmd-slice-003-report.md` (estrutura canônica do skill).

## 12. Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy.
Produce failing-before evidence, implement the smallest correct change, run focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
