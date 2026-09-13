# Design — unit-labels-env

## D1 — Envs com default canônico

`HMD_UNIT_1_LABEL`/`HMD_UNIT_2_LABEL` lidas em `config/settings/base.py`
(`strip()`; vazio → default "Unidade 1"/"Unidade 2"). Sem validação de
unicidade/comprimento além de `strip` — labels operacionais, exibidos como
fornecidos. Dev/test herdam os defaults (nenhum env obrigatório novo).

## D2 — Fonte única: `apps/cases/units.py`

Helper novo `unit_labels() -> dict[int, str]` ({1: label1, 2: label2}) e
`unit_label(value: int) -> str`, lendo de `django.conf.settings`
(`UNIT_LABELS` definido no base.py a partir das envs). O app `cases` já é a
casa de `SchedulingUnit` e é importado por scheduler/intake/dashboard — sem
novo acoplamento. As duas tabelas duplicadas `_UNIT_LABELS` (scheduler/views
e presenters) e o dict inline do dashboard passam a chamar o helper.

## D3 — Resposta final da unidade 2: constante → função

`REPLY_UNIT_2_TEXT` vira `reply_unit_2_text()` (template-f string com o label
configurado), mantendo a forma canônica com os defaults. O texto é composto
**no momento da postagem**; eventos já gravados ficam imutáveis (append-only
— histórico preserva o texto vigente à época, que sempre pode ser reexibido
como está). A spec `scheduling` é MODIFIED porque hoje ela fixa o texto
"exatamente" com "Unidade 2" literal.

## D4 — Superfície de exibição via context processor

`unit_labels` entra como context processor global em
`apps/accounts/context_processors.py` (mesmo padrão do badge de notificações)
expondo `unit_labels.unit_1`/`unit_labels.unit_2` a TODOS os templates —
manual (`templates/accounts/manual.html`), help text de intercorrência
(`templates/scheduler/case_detail.html`) e qualquer ponto futuro. O dashboard
troca o dict inline pelo helper (o context do dashboard já fornece
`unit_labels`; o processor global não conflita — mesmo valor).

## D5 — `SchedulingUnit.IntegerChoices` intocado

Os labels das choices são o canônico/default (usados por `get_FOO_display`
em admin, por exemplo) e a migration 0008 congela o histórico — nunca editar
migrations. Exibição de produto usa o helper; `get_scheduled_unit_display()`
contince válido (mostra o canônico). Validação de entrada não muda:
`unit ∈ {1, 2}` (int).

## D6 — Ops

`.env.example` documenta as duas envs com defaults; `docker-compose.prod.yml`
repassa `${HMD_UNIT_1_LABEL:-Unidade 1}`/`${HMD_UNIT_2_LABEL:-Unidade 2}`
(seguem o padrão dos defaults do compose). README não precisa mudar (não
descreve labels de unidade).
