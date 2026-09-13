# Slice 001 — Núcleo: envs de labels, helper central e resposta final interpolada

## Objetivo

As unidades de agendamento ganham labels configuráveis por ambiente
(`HMD_UNIT_1_LABEL`/`HMD_UNIT_2_LABEL`, defaults "Unidade 1"/"Unidade 2") com
fonte única de resolução (`apps/cases/units.py`). A resposta final ao NIR da
unidade 2 passa a interpolar o label configurado (constante → função) e o
formulário de confirmação exibe os labels configurados. `SchedulingUnit`,
validação `unit ∈ {1, 2}`, FSM e migrations ficam intocados.

## Contexto necessário (ler antes de editar)

- `config/settings/base.py` — seção do intranet guard (~108-120) para o padrão
  de leitura de envs com default e `strip()`.
- `apps/cases/models.py:85-93` — `SchedulingUnit` (IntegerChoices; NÃO editar).
- `apps/scheduler/services.py:44-58` — `REPLY_UNIT_2_TEXT` e onde é usado
  (função de confirmação; o texto vai ao payload do evento/post da thread).
- `apps/scheduler/forms.py:36-60` — `_UNIT_CHOICES` (labels literais) e o
  campo `unit` do `SchedulerConfirmForm`.
- `openspec/changes/unit-labels-env/design.md` — D1 (envs/default), D2
  (helper), D3 (constante→função, histórico imutável), D5 (choices intocadas).
- `apps/scheduler/tests/test_services.py` — testes existentes da resposta
  final (citam o texto exato; devem continuar verdes com os defaults).
- `.env.example:120-127` — padrão de documentação de envs do guard (comentário
  curto + `KEY=default`).
- `docker-compose.prod.yml` — serviço `web` (~:85-100): padrão
  `KEY: ${KEY:-default}`.

## Requisitos verificáveis

- **R1** — `config/settings/base.py` define `UNIT_LABELS = {1: ..., 2: ...}`
  a partir de `HMD_UNIT_1_LABEL`/`HMD_UNIT_2_LABEL` (strip; vazio/ausente →
  "Unidade 1"/"Unidade 2").
- **R2** — `apps/cases/units.py` novo com `unit_labels() -> dict[int, str]` e
  `unit_label(value: int) -> str` lendo de `settings.UNIT_LABELS` (uma linha
  de docstring explicando que é a fonte única; sem cache/estado).
- **R3** — `apps/scheduler/services.py`: `REPLY_UNIT_2_TEXT` é substituído por
  `reply_unit_2_text()` que interpola o label; a confirmação na unidade 2
  posta o texto com o label vigente. **Teste novo**: com
  `override_settings(UNIT_LABELS={1: "Unidade 1", 2: "Unidade Satélite"})` a
  resposta postada cita "Unidade Satélite"; sem override o texto é
  byte-a-byte o atual.
- **R4** — `apps/scheduler/forms.py`: `_UNIT_CHOICES` (ou equivalente) derive
  os labels do helper. **Teste novo**: com override, o campo `unit` do
  `SchedulerConfirmForm` renderiza as labels configuradas; sem override, as
  atuais.
- **R5** — `.env.example` documenta as duas envs (comentário curto citando os
  defaults) e `docker-compose.prod.yml` repassa
  `HMD_UNIT_1_LABEL: ${HMD_UNIT_1_LABEL:-Unidade 1}` /
  `HMD_UNIT_2_LABEL: ${HMD_UNIT_2_LABEL:-Unidade 2}` no serviço `web`.
- **R6** — Nenhuma mudança em `apps/cases/models.py`, migrations, FSM ou
  validação (`unit ∈ {1, 2}`); testes existentes do services/forms continuam
  verdes sem edição (defaults preservam os textos).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/settings/base.py` | `apps/scheduler/tests/test_unit_labels.py` (novo): settings resolvem defaults e envs (via `monkeypatch.setenv` + reload do módulo de settings OU teste do helper com override; ver nota abaixo) |
| R2 | `apps/cases/units.py` (novo) | `test_unit_labels.py`: `unit_label(1/2)` devolve labels de `settings.UNIT_LABELS`; defaults quando ausentes |
| R3 | `apps/scheduler/services.py` | `test_unit_labels.py::test_reply_unit_2_uses_configured_label` (override + texto exato) e `…::test_reply_unit_2_default_text_unchanged` (sem override, texto canônico) |
| R4 | `apps/scheduler/forms.py` | `test_unit_labels.py::test_confirm_form_choices_use_labels` (override; asserção nas choices do campo) |
| R5 | `.env.example`, `docker-compose.prod.yml` | `rg -n "HMD_UNIT_(1\|2)_LABEL" .env.example docker-compose.prod.yml` — 2 matches em cada |
| R6 | — | suíte do scheduler sem edição: `TEST_DB_PORT=55435 uv run pytest apps/scheduler -q` |

Nota (R1): settings são lidos uma vez no import — para o TESTE de envs, prefira
testar o helper com `override_settings(UNIT_LABELS=...)` (unidade de
configuração é o `UNIT_LABELS`); o caminho env→settings pode ser um teste
simples chamando a MESMA função de parsing que o base.py usa (ex.: extrair
`_parse_unit_labels(os.environ)` para uma função pura testável) — evite
reload de módulo de settings nos testes.

## Escopo e expected blast radius

```yaml
expected_files:
  - config/settings/base.py
  - apps/cases/units.py
  - apps/scheduler/services.py
  - apps/scheduler/forms.py
  - apps/scheduler/tests/test_unit_labels.py
  - .env.example
  - docker-compose.prod.yml

allowed_incidental_files:
  - apps/cases/tests/__init__.py  # apenas se necessário para pacote de tests já existente — NÃO é esperado (testes ficam em scheduler)

out_of_scope:
  - apps/cases/models.py e migrations (SchedulingUnit intocado)
  - views/presenters/dashboard/templates/context processor (slice 002)
  - manual do usuário
  - mudanças na validação unit ∈ {1,2} ou na FSM
  - README
```

Escalamento: se a resposta final da unidade 2 for consumida em mais lugares
do que `services.py` (grep `REPLY_UNIT_2_TEXT`), ou se algum teste existente
quebrar por razão diferente de label, **pare e reporte**.

## Notas de implementação

- `reply_unit_2_text()` deve compor o texto NA CHAMADA (não cachear em
  constante de módulo importada no load).
- Mantenha o texto canônico com os defaults idêntico ao atual, incluindo a
  ausência de ponto final.
- Siga o padrão de type hints do repo (`-> str`, `dict[int, str]`).

## Plano de testes

### RED

- comando: `TEST_DB_PORT=55435 uv run pytest apps/scheduler/tests/test_unit_labels.py -q`
- falha esperada: coleção falha (`test_unit_labels.py` ainda não existe) OU,
  se você escrever os testes antes do código (ordem correta do TDD), os
  testes de R3/R4 falham porque a resposta final usa a constante com
  "Unidade 2" literal e o form renderiza labels fixos.

### GREEN

- mesmo comando — 0 failed.

### Verificação do slice

- `TEST_DB_PORT=55435 uv run pytest apps/scheduler -q` — 0 failed (suíte do
  app inteira, incluindo os testes existentes que citam o texto canônico).
- `uv run ruff check config/settings/base.py apps/cases/units.py apps/scheduler && uv run ruff format --check config/settings/base.py apps/cases/units.py apps/scheduler` — ok.
- `uv run mypy apps` — ok.
- `docker compose -f docker-compose.prod.yml --env-file .env.example config --quiet` (envs dummy de secrets se exigidas pelo host: use os `_FILE` apontando para arquivos temporários; se falhar por falta de secrets no seu ambiente, reporte e valide apenas a sintaxe YAML do diff).

## Critérios de aceitação

- [ ] R1–R6 verdes conforme a matriz
- [ ] Nenhum arquivo fora do blast radius
- [ ] RED demonstrado antes do GREEN (mesmo comando)
- [ ] Texto canônico inalterado com os defaults (teste existente sem edição passa)
