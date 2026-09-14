# Design: role-labels-ptbr

## Context

Os papéis vivem como chaves inglesas em `Role.name` (seed), `active_role` de
sessão, guards (`@role_required`), dispatcher da home e middleware. A UI
imprime essas chaves — e também `actor_role`/`author_role` das trilhas de
eventos e comunicações — diretamente. Superfícies identificadas (verificadas
na review do plano):

1. `templates/base.html:82` — badge "Papel ativo" `{{ active_role }}`
2. `templates/accounts/home.html:20` — badges `{{ name }}`
3. `templates/accounts/switch_role.html:19` — botões `{{ role }}`
4. `templates/accounts/profile.html:27` — `{{ user_roles|join:", " }}`
5. `templates/doctor/case_detail.html:85` — "papel `{{ decision_event.actor_role }}`"
6. `templates/doctor/case_detail.html:317` — trilha "papel `{{ event.actor_role }}`"
7. `templates/intake/case_detail.html:305` — trilha "papel `{{ event.actor_role }}`"
8. `templates/intake/case_detail.html:341` — badge `{{ communication.author_role }}`
9. `templates/scheduler/case_detail.html:248` — badge `{{ communication.author_role }}`

Fora de escopo (verificado): `notifications.html`, `login.html`,
`intranet_blocked.html`, `dashboard/home.html`, filas, mensagens flash,
constantes de notificação e o admin Django (staff-only, mostra a chave por
design técnico). O manual já usa "Médico"/"Agendador"/"NIR" — sem edição.

## Goals / Non-Goals

- **Goal**: rótulos em português nas 9 superfícies, com fonte única e fallback
  seguro; chaves inglesas intocadas em todo o resto.
- **Non-goal**: i18n completa, tradução de conteúdos clínicos, renomear dados.

## Decisions

### D1 — Fonte única em módulo puro, filtro de template como primitiva de render

`apps/accounts/role_labels.py`:

```python
ROLE_LABELS = {
    "doctor": "médico",
    "scheduler": "agendador",
    "manager": "supervisor",
    "nir": "nir",
    "admin": "admin",
}

def role_label(value: str) -> str:
    return ROLE_LABELS.get(value, value)
```

Filtro `role_label` em `apps/accounts/templatetags/role_labels.py`
(`register.filter`) — primeiro templatetags do repo. Descoberta garantida:
`TEMPLATES` com `APP_DIRS=True` e SEM override de `loaders`
(`config/settings/base.py:169-186`), logo não há armadilha de cached loader.
`Role.name` é `CharField` puro sem `choices` (`apps/accounts/models.py:40`) —
não existe `get_FOO_display`; módulo puro + filtro é a costura mínima
(precedente `apps/cases/units.py` usou processor porque compõe em Python nas
views; aqui não há composição). `nir`/`admin` entram no mapa EXPLICITAMENTE
(assert do mapeamento completo, não implícito pelo fallback).

### D2 — Tradução só na renderização; chave crua continua no wire e nos dados

`switch_role.html`: o `value` do input hidden permanece `{{ role }}` (chave);
apenas o texto do botão vira `{{ role|role_label }}`. `actor_role`/
`author_role` continuam gravados como chaves (`cases/models.py`,
`cases/communications.py`); o filtro é aplicado apenas na exibição. Nenhum
permissionamento muda de semântica.

### D3 — Fallback = chave crua (inclui `system`)

Chave fora do mapeamento (ex.: `system` das trilhas, `nurse` de teste, seed
futuro) exibe a própria chave em vez de quebrar ou esconder o papel.
Cenários pinados na spec (home com `nurse`, trilha com `system`).

### D4 — `{% load %}` em TODA template que usa o filtro

`{% load %}` não é herdado por templates filhos (semântica Django): cada uma
das 7 templates afetadas carrega `role_labels` explicitamente (base.html +
6 demais). Sem o load, `TemplateSyntaxError` alto — falha visível, não
silenciosa.

## Risks / Trade-offs

- Asserts de HTML que esperavam chaves podem ficar VACUOS (comentários HTML
  em `base.html` contêm as chaves cruas: `assert "doctor" in body` continuaria
  verde via comentário). Mitigação no slice: asserts de rótulo são
  badge-scoped (`>médico<`) e os asserts de chave migram para o input
  (`value="doctor"`) ou sessão — nunca substring solta no body.
- Superfície pequena e grep-auditável; sem risco de chave/rótulo trocados no
  wire (D2 pinado por teste E2E da troca de papel).

## Open Questions

Nenhuma — mapeamento confirmado pelo dono (doctor→médico, scheduler→
agendador, manager→supervisor; nir/admin ficam como estão).
