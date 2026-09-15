# Design: painel-ats-parity

## Contexto — decisões do dono (2026-09-15)

- **Política de PHI corrigida**: zero-PHI é do **perímetro externo**
  (LLMs/serviços de terceiros — tokenização determinística, guard). A UI
  interna (funcionários do hospital) mostra a identificação completa.
  A lista do painel REVERTE o zero-PHI; métricas seguem contagens.
- Cards do painel: nome, idade, **unidade de origem**, exame solicitado,
  fase do fluxo, data/hora de inserção, botão [Detalhes] (igual ats-web).
- Default da lista: **hoje, todos os estados**.
- Tipo de exame: dropdown simples (catálogo HMD).
- Trilha: sai do NIR/médico; erro vira badge; vive no detail do painel,
  jargão simplificado, collapsible.

## D1 — Unidade de origem (extração + campo)

- `extract_header_metadata` ganha `origin_unit: str | None`: parser
  linha a linha de `Unid. Origem:` — valor na MESMA linha OU, rótulo
  sozinho, na linha imediatamente seguinte (não-vazia, sem ser rótulo
  SESAB conhecido do `_FIELD_BREAK`/outro rótulo de cabeçalho — layout
  real: rótulo L31, valor L32 de ~7 palavras institucionais). Primeira
  ocorrência vence; strip; truncada a 128 chars (defesa).
- `Case.origin_unit` (CharField(128), blank/default ""). Persistido pelo
  worker pdf junto dos metadados; zerado no reenvio
  (`_RESUBMIT_CLEARED_FIELDS` + `""`); sobrevive ao CLEANED (administrativo,
  paridade `agency_record_number`).
- Não é PHI sensível (dado institucional), mas segue o mesmo ciclo dos
  metadados por simplicidade e coerência.

## D2 — Lista do painel (paridade ats-web)

- **Cards**: nome (`patient_name`, `—` quando ausente) + idade
  (`is not None`, `0 a` exibe) + **unidade de origem** (`origin_unit`)
  + exames declarados (tipos, como hoje) + **fase** (próximo passo do
  `CASE_NEXT_STEP_LABELS` + status badge, como hoje) + **data/hora de
  inserção** (`created_at` d/m/Y H:i, absoluto) + **botão [Detalhes]**
  (link para `dashboard:case_detail`). Nº de ocorrência permanece. Ação
  de encerramento administrativo MIGRA para o detail (link no card sai;
  o fluxo/validação do encerramento permanece intacto).
- **Filtros (compoem por AND, molde ats-web `_dashboard_case_list_context`)**:
  - `date_from`/`date_to` (`created_at__date` gte/lte, formato ISO);
  - `status` (vigente);
  - `procedure_type` (dropdown «Tipo de exame»: `declared/all` default;
    filtro `procedures__declared_by_nir=True,
    procedures__procedure_type=X` + `distinct()` — mesma técnica do
    filtro por subtipo da fila médica);
  - `q` (busca ≥3 chars: `patient_name__icontains` OU nº de ocorrência
    OU prefixo do uid — a busca por NOME entra agora; volume baixo, sem
    índice trigram);
  - `scope` (ativos/todos) permanece — **default muda para `todos`**.
- **Default (molde ats-web `_resolve_list_defaults`)**: sem NENHUM filtro
  explícito (`scope`, `status`, `procedure_type`, `q`, `date_from`,
  `date_to`) → `date_from=date_to=hoje`, `scope=todos` (recebidos hoje,
  todos os estados, incluindo CLEANED); qualquer filtro explícito é
  preservado. O `period` das MÉTRICAS permanece independente (hoje/7d/
  30d/tudo, default hoje) — como no ats-web (métricas por período,
  lista por datas próprias).
- Ordenação `-created_at` e paginação (25) preservadas, com filtros na
  query string.

## D3 — Detail do caso no painel + trilha legível

- Rota `dashboard:case_detail` (manager/admin; 404 para inexistente —
  sem vazar): identificação completa (nome, idade, sexo, raça, unidade de
  origem, nº de ocorrência, data/hora de inserção, fase/status),
  procedimentos declarados, e a **trilha de eventos** — novos módulos
  `apps/dashboard/event_labels.py` com `EVENT_LABELS: dict[str, str]`
  cobrindo TODOS os tipos de `CaseEventType` (~23 — catálogo exato no
  slice; molde ats-web: `EVENT_LABELS.get(event_type, event_type)`,
  fallback = o valor bruto) e `EVENT_BADGE_CSS` (dots por categoria:
  sistema/usuário/erro). Evento com payload de erro → dot vermelho.
- **Collapsible**: card «Trilha de eventos» com `data-bs-toggle="collapse"`
  FECHADO por padrão (molde ats-web); conteúdo: data/hora, rótulo legível,
  ator (papel quando houver).
- **Encerramento administrativo**: o detail hospeda o botão/link para as
  rotas `admin_close_confirm`/`admin_close` EXISTENTES (caso ≠CLEANED) —
  fluxo, gates e validações intocados.

## D4 — Trilha sai do NIR e do médico + badge de erro

- `templates/intake/case_detail.html` e `templates/doctor/case_detail.html`:
  bloco «Trilha de eventos» REMOVIDO (as views deixam de montar `events`
  para esses templates).
- Badge de erro: quando `status == FAILED`, o detalhe NIR exibe badge
  `danger` «Falha no processamento» (o motivo técnico permanece acessível
  pela trilha do painel); idem no detalhe médico quando aplicável.
- Specs ajustadas: «Caso decidido consultável» (corpo sem trilha),
  «Presenter re-identificado» (cenário novo: sem trilha), «Meus casos e
  detalhe do NIR» (detalhe sem trilha + badge de erro).

## D5 — Não-mudanças

- Guard `_assert_tokens_only`, anonimização e todo o perímetro externo —
  intocados (a política corrigida NÃO os afeta).
- Métricas do painel (contagens), fila médica/agendador/meus casos
  (changes recentes), notificações, PWA — intocados.
- Atualização parcial sem reload (`X-ATS-Partial` do ats-web) e badge de
  «atenção» (casos suspeitos) — fora de escopo (não pedidos; registrar
  como possíveis futuros).
