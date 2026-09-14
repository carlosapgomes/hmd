# Design: role-labels-ptbr

## Context

Os papéis vivem como chaves inglesas em `Role.name` (seed), `active_role` de
sessão, guards (`@role_required`), dispatcher da home e middleware. A UI,
porém, imprime essas chaves diretamente (badge `{{ active_role }}` em
`base.html:82`, `{{ name }}` em `accounts/home.html:20`, `{{ role }}` nos
botões de `accounts/switch_role.html:19`, `{{ user_roles|join:", " }}` em
`accounts/profile.html:27`). O piloto pede os termos em português.

## Goals / Non-Goals

- **Goal**: rótulos em português NAS 4 superfícies, com fonte única e fallback
  seguro; chaves inglesas intocadas em todo o resto.
- **Non-goal**: i18n/reverse de URL por papel, tradução de conteúdos clínicos,
  renomear qualquer dado.

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
(`register.filter`). Precedente: `apps/cases/units.py` + processor para
unidades — aqui o filtro é menor e suficiente porque cada template já recebe
os valores que precisa; não há composição em Python a alimentar. `nir` e
`admin` entram no mapa EXPLICITAMENTE (assert do mapeamento completo, não
implícito pelo fallback).

### D2 — Tradução só na renderização; chave crua continua no wire

`switch_role.html`: o `value` do input hidden permanece `{{ role }}` (chave);
apenas o texto do botão vira `{{ role|role_label }}`. Badge e listas idem.
Nenhum teste/permissionamento muda de semântica.

### D3 — Fallback = chave crua

Papel desconhecido (ex.: seed futuro) exibe a própria chave em vez de quebrar
ou esconder o papel do usuário. Cenário pinado na spec.

## Risks / Trade-offs

- **Baixo**: risco de alguém exibir rótulo onde precisa de chave. Mitigação:
  filtro aplicado APENAS nas 4 superfícies listadas; grep do slice confirma.
- Asserts antigos `"doctor" in body` (páginas de seleção/perfil) ficarão
  verdes ou falharão conforme a página — o slice migra cada um
  conscientemente (HTML→rótulo; sessão/contexto→chave).

## Open Questions

Nenhuma — mapeamento confirmado pelo dono (doctor→médico, scheduler→
agendador, manager→supervisor; nir/admin ficam).
