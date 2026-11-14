# Design: case-detail-human-reference

## Contexto (evidências levantadas)

- `templates/intake/case_detail.html:4,12,19` — `<title>`, breadcrumb e `<h1>`
  renderizam `case.case_id` (UUID completo); a página não exibe nenhum campo do
  paciente (`grep patient_name` = 0). A view `apps/intake/views.py:367-430`
  passa o objeto `case` + listas, sem identificação.
- `templates/doctor/case_detail.html:4,12`, `templates/doctor/decide.html:4,13`,
  `templates/scheduler/case_detail.html:5,13` — `<title>`/breadcrumb com
  `case_id` (UUID) vindo dos presenters (`apps/doctor/presenters.py:713`,
  `apps/scheduler/presenters.py:115`).
- `templates/doctor/_case_cards.html:161` — badge `Caso {{ prior.prior_case_id }}`
  (UUID do caso prévio; dict construído em `apps/doctor/presenters.py:451-473`).
- Fluxos textuais do NIR com UUID: `apps/intake/views.py:295,459,540,636,642,690`
  (flash messages) e `templates/intake/home.html:28` (alerta de lote);
  `templates/intake/corrected_resubmission.html:12,22`;
  `templates/intake/case_detail.html:106` (link de correção).
- `corrections` no detalhe do NIR é lista de objetos `Case`
  (`apps/intake/views.py:404` — reverso `corrected_by`) → o template já acessa
  qualquer propriedade nova do model sem mudança de view.
- O painel já usa o padrão-alvo: nº preferido, fallback `uid {{ short_id }}`
  com `short_id = str(case.case_id)[:8]` (`apps/dashboard/views.py:308,359,434`).
- `Case.agency_record_number` (`apps/cases/models.py:124`) não é único nem
  obrigatório — extraído do PDF; reenvio corrigido gera caso novo que
  compartilha o nº do original.

## D1 — Helper de referência no model (fonte única)

Propriedades puras em `Case` (`apps/cases/models.py`), sem migration:

```python
@property
def short_id(self) -> str:
    return str(self.case_id)[:8]


@property
def display_reference(self) -> str:
    """Nº de ocorrência; fallback `uid <8 chars>` (padrão do painel)."""
    return self.agency_record_number.strip() or f"uid {self.short_id}"


@property
def disambiguated_reference(self) -> str:
    """`nº · <8 chars>` quando há nº (distingue reenvio corrigido do original)."""
    record = self.agency_record_number.strip()
    return f"{record} · {self.short_id}" if record else f"uid {self.short_id}"
```

Justificativa: 3 apps e 8+ superfícies precisam da mesma regra de fallback;
duplicar em template/filter espalharia a lógica de apresentação. Property no
model é pura, testável e visível no template (`{{ case.display_reference }}`).

## D2 — Detalhe do NIR: card de identificação (padrão scheduler/doctor)

- Hero atual (`intake/case_detail.html:17-36`) é substituído por card
  «Identificação do paciente» espelhando `templates/scheduler/case_detail.html:18-40`:
  header com `<h1>` «Identificação do paciente» + badge de status (as classes
  condicionais do badge atual — `FAILED` danger, revisão warning, etc. — são
  preservadas no header), body com `dl`: Paciente (nome, `—` quando ausente),
  Idade (`84 a`, omitida quando nula — padrão `my_cases.html:64`), Nº de
  ocorrência (`display_reference` já cobre o fallback), e linha muted com
  criado/em atualizado (timestamps atuais migram para o body).
- Conteúdo mínimo (decisão do dono): **nome + idade** — não a demografia
  completa (sexo/raça/nascimento) do médico/agendador.
- `<title>` (`:4`), breadcrumb (`:12`) e o link de «Reenvios corrigidos»
  (`:106`) passam a usar `case.display_reference` /
  `correction.disambiguated_reference`. O objeto `case` já está no contexto —
  nenhuma mudança de view é necessária para o template.

## D3 — Desambiguação (`nº · sufixo curto`)

Pontos onde a duplicidade de nº é estrutural (reenvio corrigido + original):

1. Link «Reenvios corrigidos» no detalhe (`case_detail.html:106`) —
   `correction.disambiguated_reference` (corrections são `Case`).
2. Página «Reenviar corrigido» (`corrected_resubmission.html:12,22`) — o
   breadcrumb-link e o parágrafo citam `case.disambiguated_reference`.
3. Flash «Reenvio corrigido criado» (`views.py:540`) — cita
   `new_case.disambiguated_reference` (novo caso coexiste com o original).
4. «Meus casos» — quando a página contém 2+ casos com o mesmo nº, os cards
   afetados exibem `N · <sufixo>` ao lado do nº; nº único permanece limpo.
   Implementação: a view computa `duplicate_numbers` (set de nºs com
   `count > 1` na queryset da página) e o template renderiza o sufixo
   condicionalmente. Lógica na view (não no template), barata (uma query
   `values+annotate` ou `Counter` sobre a lista já carregada).

Demais flashes e o alerta da home usam `display_reference` simples (criações
de lote têm nºs distintos entre si).

Distinção de contrato: a linha rotulada «Nº de ocorrência» dos cards
(my_cases/queues) mantém `—` quando o nº está ausente — decisão vigente de
`queues-remove-uid` (card sem nº fica só com a identificação do paciente). O
fallback `uid <8 chars>` é das **referências** (title/breadcrumb/cabeçalho do
detalhe/mensagens), não da linha rotulada.

## D4 — Doctor e scheduler: referência no contexto raiz

- `apps/doctor/presenters.py` (`build_case_detail_context`, ~`:713`) e
  `apps/scheduler/presenters.py` (`:115`): adicionar chave raiz
  `"case_reference": case.display_reference`. Templates usam
  `{{ case_reference }}` no `<title>`/breadcrumb/link (decide incluído — a
  view de decisão reutiliza o mesmo presenter/contexto de `case_id`).
- Badge prior-case (`_case_cards.html:161`): `_build_prior_cases` ganha
  `"prior_case_reference"` — lookup read-only do `Case` prévio com
  `.only("case_id", "agency_record_number")` (a query só roda quando há
  prior-case; `prior_case_id` permanece no dict — tests atuais pinam).
- `identification.*` existente permanece inalterado.

## D5 — Não-mudanças

- **Painel manager/admin**: inalterado (padrão próprio vigente: nº com
  fallback `uid <curto>` — é exatamente o fallback que este change generaliza).
- **URLs/rotas**: uid permanece como chave (`href`/`action`) — mudança é só de
  texto visível. Asserts de teste que localizam card pelo uid no HTML continuam
  válidos (uid segue no href).
- `Case.__str__`, logs, admin, busca do painel por uid: fora do escopo.
- FSM, models de persistência, migrations: nenhum.

## Riscos

- `agency_record_number` vazio (gate): coberto pelo fallback `uid <8>`
  (pinado em cenário de spec) — nas referências; nos cards, `—` vigente.
- Campos deferred: views de ack/gate carregam o caso com
  `.only("pk", "case_id", "created_by_id")` (`apps/intake/views.py:446,623,664`)
  — os flashes leriam `agency_record_number` como campo deferred (query
  implícita). Slice 003 acréscita o campo aos três `.only()`.
- Quebra de pins de teste que assertam `Caso <uid>`: mapeados por slice
  (test_case_detail, test_creation, test_gate_actions, test_closure_views,
  test_corrected_resubmission, test_my_cases, doctor/test_detail,
  test_decision, scheduler/test_views); slices atualizam os pins afetados.
