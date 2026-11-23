# Design

## Context

- `User` já possui o par opcional `professional_council` (`COREN`/`CRM`) +
  `professional_council_number` com validação par-ou-nada em `clean()` e a
  property `professional_registration_display` (`"CRM 12345"` ou `""`) —
  desde a migration `0001_initial`, clone do padrão ats-web. O par é a fonte
  canônica; não há campo novo nem migration nesta change.
- `Case.doctor_decided_by` (FK `SET_NULL`) é carimbado no MESMO atomic da
  decisão médica (`apps/cases/procedures.py::record_doctor_procedure_decisions`)
  — por construção, todo caso que chega ao agendamento ou a `DOCTOR_DENIED`
  tem o decisor carimbado.
- Mensagens finais ao NIR são rows append-only de `CaseCommunicationMessage`
  compostas por: `REPLY_UNIT_1_TEMPLATE`/`reply_unit_2_text()`
  (`apps/scheduler/services.py::confirm_case_scheduling`) e
  `DENIAL_REPLY_TEMPLATE` (`apps/cases/closure.py::post_doctor_denial_reply`).
- Políticas do dono (2026-11-23): reusar o par existente (6.1); degradação
  graciosa sem registro; política operacional é DESABILITAR usuários, não
  deletar (6.2 — `doctor_decided_by` nulo é edge teórico tratado por
  omissão); escopo = confirmação U1+U2 e negativa médica apenas (6.3).

## Decisões

### D1 — Reuso do par existente, sem migration

Criar `professional_registration_number` duplicaria dado, quebraria a
invariante par-ou-nada e perderia a distinção CRM×COREN. Os campos existem e
a property de formatação existe; a change apenas os destrava (admin) e os
consome (serviços). Nenhuma migration.

### D2 — Fonte única do médico e formato da linha de crédito

Um único helper compõe a linha a partir de `case.doctor_decided_by`
(autor do post permanece quem opera: agendador nas confirmações, médico na
negativa — a linha referencia o DECISOR, exigência SUREM):

```text
com registro:    "Médico responsável: {display_name} — {professional_registration_display}"
sem registro:    "Médico responsável: {display_name}"
sem decisor:     "" (linha omitida; nunca bloqueia a postagem)
```

`display_name` (`get_full_name()` ou username) e
`professional_registration_display` já existem no `User`; o helper aplica
`.strip()` defensivo ao ler o registro formatado (o campo é texto livre
preenchido à mão no admin). Nota operacional: para usuários AD o fallback de
`display_name` é o CPF (`username`) — o provisionamento médico DEVE preencher
`first_name`/`last_name` para a linha exibir nome, não CPF. O helper vive em
`apps/cases` (núcleo dono do contrato de comunicações) e é consumido pelos
dois apps de serviço — dependência unidirecional preservada
(scheduler/closure → cases). Na negativa, `post_doctor_denial_reply` também
usa `case.doctor_decided_by` (coincide com o `user` autor por construção;
fonte única evita dois caminhos).

### D3 — Escopo das mensagens

Recebem a linha (anexada como ÚLTIMA linha do corpo, separada por `\n`):
confirmação unidade 1, confirmação unidade 2 (`reply_unit_2_text()` segue
como texto-base inalterado; a composição acontece na postagem, preservando a
assinatura pública sem argumentos e o rótulo vigente da unidade) e resposta
final da negativa médica. Ficam de fora: `REPLY_DENY_TEMPLATE` (negação de
agendamento), `REPLY_REOPEN_TEMPLATE` (intercorrência), previews/títulos de
notificação (`FINAL_REPLY_PREVIEW_BY_SOURCE` permanecem fixos e sem nome —
zero PHI adicional na superfície de sino).

### D4 — Snapshot no post, thread imutável

A linha é composta NA POSTAGEM: mensagens antigas não se reescrevem se o CRM
for preenchido depois (thread append-only é contrato do change 03). Exibição
render-time do dado corrente (padrão ats-web `doctor_display` em card) é
backlog registrado, fora do escopo.

### D5 — Renderização multilinha da thread

Os 3 templates que renderizam a thread (`intake/case_detail.html`,
`intake/closed_case_detail.html`, `scheduler/case_detail.html`) usam
`{{ communication.body }}` sem filtro — `\n` colapsa no HTML. Sem correção, a
linha de crédito e os itens da negativa (já multilinha hoje) colam
visualmente. Aplica-se `|linebreaksbr` nos 3 pontos — mudança intencional de
apresentação (itens da negativa passam a separar por linha, coerente com o
contrato textual da row). O corpo armazenado não muda (`\n`).

### D6 — Provisionamento via Django admin

Fieldset «Profissional» (`professional_council`,
`professional_council_number`) no `UserAdmin` — `fieldsets` e
`add_fieldsets` — espelhando o ats-web. A validação par-ou-nada do modelo
resolve via `clean()` no formulário do admin (erro aponta o campo pendente).
Sem UI self-service (atribuição administrativa, como `specialties`).

### D7 — Sem impacto nas fronteiras sensíveis

- FSM, locks, atomics e ordem de eventos intactos — a composição do corpo é
  pura (leitura do FK já carimbado; um acesso ao FK sob lock, sem escrita
  adicional).
- Anonimização/pipeline LLM intocados: nome+CRM em thread interna está
  dentro da política de PHI (zero-PHI é exigência do perímetro externo).
- Guards/papéis intocados.

## Riscos e trade-offs

- **Specs pinnadas**: `scheduling` fixava "exatamente" o texto da unidade 2 —
  reconciliado no delta (texto-base preservado + linha anexada).
- **Testes com igualdade estrita** de corpo que quebram (devem ser
  atualizados para o novo corpo completo): `apps/cases/tests/test_closure_denial.py`
  (negativa — carimba decisor via `record_doctor_procedure_decisions`) e
  `apps/scheduler/tests/test_views.py::1478` (igualdade da lista de corpos da
  unidade 2 — o factory do views carimba decisor). Testes de serviço de
  confirmação (`test_services.py::199/245`) criam casos SEM decisor
  (`_case_in_state`, `user=None`) e permanecem verdes pela degradação
  graciosa — pins implícitos da omissão; `test_services.py::381` (negação de
  agendamento) é pin de não-regressão do escopo e NÃO deve ser alterado.
- **Manual de usuário** descreve a resposta da unidade 2 como texto fixo —
  S2 atualiza o parágrafo para citar a linha do médico responsável (ajuste
  mínimo em `templates/accounts/manual.html`, sem pin de teste).
- **Edge `doctor_decided_by` nulo**: apenas se FK `SET_NULL` disparar
  (política: desabilitar). Teste negativo cobre a omissão sem falha.
- **Mensagem sem registro** não deve falhar nem bloquear o fluxo —
  degradação graciosa é a decisão 6.1; endurecimento (exigir CRM) é backlog.
