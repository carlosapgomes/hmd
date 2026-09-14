# # Slice 002 — Lista no painel + rota/UI de encerramento + resultado ao criador

## Contexto necessário

- Painel: `apps/dashboard/views.py::home` (métricas por `?period`,
  `role_required("manager","admin")`); template `templates/dashboard/home.html`.
- Presenter de "Meus casos" (intake) trata resultados por status/eventos —
  localizar e estender para o encerramento administrativo.
- SSR puro do repo: forms GET re-renderizam; confirmação em página própria
  (padrão das confirmações existentes).
- Serviço do slice 001 disponível (import direto).

## Goal

Manager vê os casos (sem PHI), filtra, e encerra com confirmação; NIR vê o
resultado; métrica nova no painel.

## Deliverables

### R1 — Lista (`apps/dashboard/views.py`, `templates/dashboard/home.html`)

- Contexto estendido: `cases` (cards D1 do design: registro/uid curto,
  status, tipos declarados, criado, decisão, próximo passo) + filtros
  `scope`/`status`/`q` (≥3 chars) + paginação 25/pág + `period` reusado.
- Sem dados de paciente em qualquer campo do card (teste pinnado: fixture
  com paciente conhecido, nome ausente do HTML).
- Ação "Encerrar administrativamente" só em casos não-CLEANED (link →
  confirmação).

### R2 — Rota/UI (`apps/dashboard/views.py`, `urls.py`, template novo)

- `dashboard:admin_close_confirm` (GET: formulário com select do catálogo +
  textarea) e `dashboard:admin_close` (POST: service + `messages.success` +
  redirect preservando filtros; erros de validação re-renderizam com
  `messages.error`).
- 403 para papéis fora de manager/admin (parametrizado); 404 caso inexistente.

### R3 — Métrica + Meus casos

- `administratively_closed` no período nas métricas (por eventos).
- Presenter de Meus casos: CLEANED + evento administrativo → resultado
  "Encerrado administrativamente — {label do motivo}" (+ texto no detalhe).

### R4 — Testes

- Lista: default ativos; `scope=todos`; `status` filtro; busca por registro
  e prefixo de uid (≥3 chars, <3 ignora); SEM nome do paciente no HTML
  (não-vacuidade: fixture com paciente real); ação só em não-CLEANED.
- Rota: encerra e volta com filtros; 403 paramétrico (nir/doctor/scheduler);
  404; texto vazio → erro e caso intacto; código inválido → erro.
- Métrica no período; Meus casos mostra o resultado ao criador (e NÃO ao
  outro NIR).

## Out of Scope

- PHI na lista (decisão registrada); partial/HTMX; detail view manager; lote.

## Expected files

- apps/dashboard/views.py
- apps/dashboard/urls.py
- apps/dashboard/tests/ (testes novos — seguir padrão existente)
- templates/dashboard/home.html
- templates/dashboard/admin_close_confirm.html (novo)
- apps/intake/presenters.py (ou onde mora o presenter de Meus casos)
- apps/intake/tests/test_my_cases.py (resultado administrativo)

## Verification (RED → GREEN, mesmo comando)

```bash
TEST_DB_PORT=55435 uv run pytest apps/dashboard apps/intake apps/cases -q
```

## Acceptance criteria

- Todos os cenários das 2 specs delta cobertos; sem PHI na lista (pinado);
  fluxo manager encerra → NIR vê; suíte completa verde; ruff/format/mypy.

## Deviations / learnings

- (preenchido na execução)
