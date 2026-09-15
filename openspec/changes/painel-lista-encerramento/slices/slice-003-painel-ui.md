# Slice 003 — Lista no painel + rota/UI de encerramento + resultado ao criador

## Contexto necessário

- Painel: `apps/dashboard/views.py::home` (métricas por `?period`,
  `role_required("manager","admin")`); template `templates/dashboard/home.html`;
  métricas em `apps/dashboard/metrics.py::compute_summary` (dict exato —
  `EMPTY_SUMMARY` e iguadades em `apps/dashboard/tests/test_metrics.py`
  ~45-51/144-150/186-192/203-209/418-424 ganham a chave nova).
- Amend declarado: `apps/dashboard/tests/test_views.py:141-155` ("Página sem
  dados de paciente") pina hoje `agency_record_number` ausente — passa a
  pinnar NOME do paciente ausente (nº de ocorrência é dado do caso e aparece
  na lista).
- "Meus casos": presenter INLINE em `apps/intake/views.py` (~284-293 — NÃO
  existe `apps/intake/presenters.py`); template
  `templates/intake/my_cases.html` (hoje sem resultado por caso).
- Serviços dos slices 001/002 disponíveis (import direto); SSR puro do repo
  (confirmação em página própria; proibido `HttpResponseForbidden(render)`
  aninhado).
- Paginação/busca: precedentes em `apps/doctor/views.py` (~241) e
  `apps/scheduler/views.py` (~190).

## Goal

Manager vê os casos (sem nome/nascimento do paciente), filtra, e encerra com
confirmação; NIR vê o resultado; métrica nova coerente no painel.

## Deliverables

### R1 — Lista (`apps/dashboard/views.py`, `templates/dashboard/home.html`)

- Contexto estendido: `cases` (cards D1: nº de ocorrência ou uid curto,
  status, tipos declarados, criado, decisão, próximo passo) + filtros
  `scope`/`status`/`q` (≥3 chars; `icontains` no nº de ocorrência OU
  `istartswith` no uid — Postgres emite `::text` sozinho) + paginação 25/pág
  + `period` reusado.
- Sem nome E SEM data de nascimento do paciente em qualquer campo do card
  (teste pinnado: fixture com paciente conhecido, nome e `patient_birth_date`
  ausentes do HTML; nº de ocorrência PRESENTE — não-vacuidade do card).
- Ação "Encerrar administrativamente" só em casos não-CLEANED (link →
  confirmação).

### R2 — Rota/UI (`apps/dashboard/views.py`, `urls.py`, template novo)

- `dashboard:admin_close_confirm` (GET: select do catálogo + textarea
  obrigatória) e `dashboard:admin_close` (POST: service +
  `messages.success` + redirect preservando filtros; validação e a recusa
  por lease viva re-renderizam com `messages.error`).
- 403 para nir/doctor/scheduler (paramétrico); 404 caso inexistente.

### R3 — Métrica + Meus casos

- `compute_summary`: chave `administratively_closed` = contagem de eventos
  `CASE_ADMINISTRATIVELY_CLOSED` com `timestamp` na janela (card do painel —
  `templates/dashboard/home.html` ganha o card); **`em_andamento` continua
  derivado da população** (`created_at` na janela):
  `em_andamento = |population − agendados − negados − (admin_fechados ∩
  population sem desfecho)|` — admin-fechado FORA da janela não subtrai
  (total=0, card=1, em_andamento=0); admin-fechado com desfecho conta só
  no desfecho (sem dupla subtração). Implementar com conjuntos de ids.
- "Meus casos": queryset com `prefetch_related("events")` (hoje só
  `procedures`); CLEANED + evento administrativo → resultado "Encerrado
  administrativamente — {label do motivo}" na aba de encerrados (código +
  texto), só para o criador.

### R4 — Testes

- Lista: default ativos; `scope=todos`; `status`; busca por ocorrência e
  prefixo de uid (≥3 chars; <3 ignora); paginação preserva filtros; SEM
  nome e SEM data de nascimento do paciente no HTML; nº de ocorrência
  presente; ação só em não-CLEANED.
- Métrica: card conta eventos na janela; os DOIS casos limítrofes pinnados
  (caso criado fora da janela e encerrado dentro → total 0, card 1,
  em_andamento 0; caso agendado E encerrado administrativamente na janela
  → conta em agendados, em_andamento não-negativo).
- Rota: encerra e volta com filtros; 403 paramétrico; 404; texto vazio →
  erro e caso intacto; código inválido → erro; lease viva → mensagem e caso
  intacto.
- Métrica no período (e sai de em_andamento); Meus casos mostra o resultado
  ao criador (e NÃO ao outro NIR).
- Amends: `test_views.py` (página sem nome E sem data de nascimento;
  docstring atualizado) e `test_metrics.py` (chave nova) verdes.
- `templates/accounts/manual.html`: bullet do Painel atualizado (deixa de
  dizer "apenas números e rótulos" — passa a descrever a lista de casos por
  nº de ocorrência + encerramento administrativo), em paridade com o ajuste
  de Purpose declarado no design (arquivamento).

## Out of Scope

- Nome/nascimento na lista (decisão registrada; flip = editar requisito +
  presenter); partial/HTMX; detail view manager (backlog item 4); lote.

## Expected files

- apps/dashboard/views.py
- apps/dashboard/urls.py
- apps/dashboard/metrics.py
- apps/dashboard/case_labels.py (novo — mapa 17 estados → pt-BR,
  fonte canônica do "próximo passo", teste de cobertura completa)
- apps/dashboard/tests/test_views.py (novos + amend declarado)
- apps/dashboard/tests/test_metrics.py (amend declarado)
- templates/dashboard/home.html
- templates/dashboard/admin_close_confirm.html (novo)
- templates/accounts/manual.html (bullet do Painel)
- apps/intake/views.py (presenter de Meus casos é INLINE aqui)
- templates/intake/my_cases.html (render do resultado)
- apps/intake/tests/test_my_cases.py (resultado administrativo — seguir
  padrão dos testes existentes de intake; se o arquivo tiver outro nome,
  criar `test_closure_views.py`-style conforme o repo)
- allowed incidental: NENHUM

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/dashboard apps/intake apps/cases apps/accounts -q
uv run python manage.py makemigrations --check --dry-run
```

## Acceptance criteria

- Todos os cenários das 2 specs delta cobertos; sem nome de paciente na
  lista (pinado, com nº de ocorrência presente); fluxo manager encerra →
  NIR vê; métrica coerente (não-conta em andamento); suíte completa verde;
  ruff/format/mypy.

## Deviations / learnings

- (preenchido na execução)
