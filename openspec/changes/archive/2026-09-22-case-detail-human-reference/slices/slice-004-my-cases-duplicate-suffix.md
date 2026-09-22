# Slice 004 — Meus casos: sufixo de desambiguação para nº duplicado

```yaml
expected_files:
  - apps/intake/views.py
  - templates/intake/my_cases.html
  - apps/intake/tests/test_my_cases.py
allowed_incidental_files: []
out_of_scope:
  - qualquer outra superfície de my_cases (badges, ordenação, abas)
  - scheduler/doctor queues (nº único por página não é garantido lá, mas a
    duplicidade estrutural vive no fluxo do NIR — decisão D3)
```

## Objetivo

Quando a página «meus casos» contém 2+ casos com o mesmo nº de ocorrência
(original + reenvio corrigido), os cards afetados exibem o nº acompanhado do
sufixo curto (`N · <8 chars>`); cards com nº único permanecem como hoje.

## Contexto necessário

- `apps/intake/views.py` — view de my_cases monta `items` com
  `agency_record_number: case.agency_record_number or "—"` (`:349`); a
  queryset da página já está materializada em lista antes do render.
- `templates/intake/my_cases.html:64-65` — linha
  `Nº de ocorrência: <strong>{{ item.agency_record_number }}</strong>`.
- Design D3: computar na VIEW (não no template) o conjunto de nºs
  duplicados da página — ex.: `Counter` sobre os nºs não-vazios dos casos
  carregados; expor por item (ex.: `item.reference_suffix` = sufixo `·
  <8 chars>` quando o nº do item duplica na página, senão `""`) OU chave
  `duplicate_numbers` + `item.case.short_id` no template. Escolher UMA
  forma e manter simples; a regra de exibição é: sufixo aparece somente
  para nº duplicado E preenchido (sem nº → fallback já coberto pela linha
  atual `—`).
- Testes: `apps/intake/tests/test_my_cases.py` — pins atuais da linha de nº
  continuam válidos para o caso único.

## Requisitos verificáveis

- **R1** — Página com dois casos do mesmo nº: ambos os cards exibem
  `N · <8 chars>` na linha de nº de ocorrência.
- **R2** — Página com nºs únicos: cards exibem apenas `N` (texto idêntico ao
  atual — sem sufixo, sem espaço extra).
- **R3** — Caso sem nº: linha permanece `—` (sem sufixo, sem crash). O fallback `uid <8 chars>` NÃO se aplica à linha rotulada dos cards — decisão vigente de `queues-remove-uid` (card sem nº fica só com a identificação do paciente); o fallback é das referências (title/breadcrumb/detalhe/flash), definidas nos slices 002/003.
- **R4** — Lógica de duplicidade vive na view (template só renderiza o que
  recebe; sem `Counter`/query no template).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/views.py`, `templates/intake/my_cases.html` | teste novo em `test_my_cases.py`: 2 casos, mesmo nº → `N · <8>` nos dois cards |
| R2 | idem | teste novo: nº único → `N` sem sufixo |
| R3 | `apps/intake/views.py` | teste novo: sem nº → `—` |
| R4 | `apps/intake/views.py` | revisão do diff (sem lógica no template) |

## Plano de testes

### RED

- Comando: `uv run pytest apps/intake/tests/test_my_cases.py -q`
- Falha esperada: o teste de duplicado falha — cards atuais exibem apenas `N`
  (sem sufixo).

### GREEN / verificação local

- `uv run pytest apps/intake/tests/test_my_cases.py -q` — exit 0
- `uv run pytest apps/intake/tests/ -q` — sem regressão no app
- `uv run ruff check apps/intake && uv run ruff format --check apps/intake` — exit 0
- `uv run mypy apps/intake/views.py` — exit 0

## Critérios de aceitação

- [ ] R1-R4 verdes
- [ ] Caso único: output do card idêntico ao de antes (R2 pina isso)
- [ ] Diff limitado a view + template + testes do módulo

## Handoff

Se a duplicidade exigir considerar paginação/abas além da página corrente,
parar e escalar ao parent (decisão de produto não prevista no design).
