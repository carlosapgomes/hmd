# Slice 003 — Trilha legível no detail do painel + remoção NIR/médico

```yaml
expected_files:
  - apps/dashboard/event_labels.py
  - apps/dashboard/views.py
  - templates/dashboard/case_detail.html
  - templates/intake/case_detail.html
  - templates/doctor/case_detail.html
  - apps/intake/views.py
  - apps/doctor/presenters.py
  - apps/dashboard/tests/test_event_labels.py
  - apps/dashboard/tests/test_views.py
  - apps/intake/tests/test_detail.py
  - apps/doctor/tests/test_detail.py
  - apps/doctor/tests/test_decision.py
```

## Contexto necessário

- `CaseEventType` (~23 tipos) em `apps/cases/events.py`; molde ats-web:
  `EVENT_LABELS: dict[str, str]` + `EVENT_DOT_CSS` (apps/dashboard/views.py
  do ats-web ~325; `EVENT_LABELS.get(e.event_type, e.event_type)`).
- Trilha hoje: `templates/intake/case_detail.html` (~297-311) e
  `templates/doctor/case_detail.html` (~305+) renderizam `events`
  (montados por `apps/intake/views.py` case_detail e
  `apps/doctor/presenters.py` `_event_summary`).
- Detail do painel (slice 002): `templates/dashboard/case_detail.html`
  com identificação completa; a trilha entra AQUI.
- Badge de erro: `status == FAILED`.

## Goal

Trilha com rótulos legíveis (collapsible fechado) no detail do painel;
NIR e médico sem trilha; caso FAILED com badge de erro.

## Deliverables

### R1 — Mapa de rótulos (`apps/dashboard/event_labels.py`)

- `EVENT_LABELS: dict[str, str]` cobrindo **TODOS** os valores de
  `CaseEventType` (~23): mapeie cada tipo para português claro no molde
  ats-web (ex.: `CASE_CREATED`→«Caso criado», `CASE_STATUS_PDF_EXTRACTING`→
  «Extração de texto iniciada», `CASE_ANONYMIZATION_COMPLETED`→«Anonimização
  concluída», decisões→«Decisão médica registrada», agendamento→…,
  `CASE_FINAL_REPLY_POSTED`→«Resposta final publicada»,
  `CASE_ADMINISTRATIVELY_CLOSED`→«Encerrado administrativamente», …).
  Fallback = valor bruto (`EVENT_LABELS.get(t, t)`).
- `EVENT_BADGE_CSS: dict[str, str]` por categoria (sistema/usuário/erro);
  evento com `status=FAILED` no payload ou tipo `*_FAILED` → danger.
- Teste de COBERTURA: todo valor de `CaseEventType` tem label (itera o
  enum — adiciona tipo novo sem label → teste falha; anti-drift).

### R2 — Trilha no detail do painel (collapsible)

- `dashboard case_detail` monta `events` (select_related actor) com
  label/dot/data/hora/ator; template: card «Trilha de eventos» com
  `data-bs-toggle="collapse"` FECHADO por padrão (`show` ausente);
  badges com `EVENT_BADGE_CSS`.

### R3 — Remoção NIR/médico + badge de erro

- `templates/intake/case_detail.html` e `templates/doctor/case_detail.html`:
  bloco da trilha REMOVIDO; views/presenter deixam de montar `events`
  para esses templates. **`decision_event` do presenter médico
  PRESERVADO** via consulta dedicada (evento
  `CASE_DOCTOR_DECISIONS_RECORDED` mais recente, query direta) — o card
  «Decisões registradas» (ator/data) continua renderizando; atualizar
  `apps/doctor/tests/test_decision.py` (testa `context["events"]`/trilha
  hoje).
- Badge de erro: `status == FAILED` → badge `danger` «Falha no
  processamento» no detalhe NIR (e no médico quando o caso falho for
  acessível). Sem motivo técnico (vive na trilha do painel).

### R4 — Testes (RED→GREEN)

- Novos (RED): cobertura do mapa (anti-drift); trilha no detail do painel
  renderiza labels legíveis (não os valores brutos dos tipos) com
  data/hora/ator; collapsible FECHADO por padrão (sem classe `show`;
  botão com `data-bs-toggle="collapse"`); tipo fora do mapa → valor bruto;
  detalhe NIR/médico SEM bloco de trilha (assert de ausência do título
  «Trilha de eventos» no HTML); caso FAILED no detalhe NIR → badge
  «Falha no processamento».
- Bateria completa do AGENTS.md + suíte.

## Gates para o reviewer (2 linhas)

1. `EVENT_LABELS` cobre 100% do enum `CaseEventType` (teste anti-drift
   iterando o enum — não lista hardcoded duplicada).
2. Nenhum template de NIR/médico renderiza `events` (grep `events` nos
   dois templates vazios de trilha; views/presenters não montam mais).

## Out of scope

- Métricas/lista (slice 002); `X-ATS-Partial`; badge de atenção; histórico
  de motivos de erro no NIR.
