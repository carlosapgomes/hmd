# Slice 002 — Meus casos do NIR com identificação do paciente

```yaml
expected_files:
  - apps/intake/views.py
  - templates/intake/my_cases.html
  - apps/intake/tests/test_my_cases.py
```

## Contexto necessário

- `apps/intake/views.py` `my_cases` (~296-320): lista os casos do criador
  com `-created_at` (histórico), cards em `templates/intake/my_cases.html`
  (uid/status/gate/tipos; hoje SEM identificação do paciente).
- Campos `patient_name`/`patient_age` existem; o NIR é o criador do caso
  (escopo por criador vigente; caso alheio 404).
- Painel manager/admin permanece zero-PHI (nenhuma mudança no dashboard).

## Goal

Cards de «Meus casos» exibem nome e idade do paciente quando presentes
(`—` no nome e idade omitida quando ausentes); ordenação histórica
intacta.

## Deliverables

### R1 — View e template

- Items da lista ganham `patient_name`/`patient_age` (do caso, sem
  transformação).
- Card: linha de identificação `{{ item.patient_name|default:"—" }}` +
  `{% if item.patient_age %} · {{ item.patient_age }} a{% endif %}`, no
  estilo visual dos cards atuais; SEM linha de tempo (histórico, não fila
  de espera).

### R2 — Testes (RED→GREEN)

- Novos (RED): card com nome+idade (`84 a`) para caso identificado; caso
  sem identificação → `—` no nome e ausência do sufixo de idade (assert
  escopado ao card, não contagem global); ordenação `-created_at`
  preservada (pinnada); caso de outro NIR continua ausente da lista
  (regressão do escopo por criador).
- Bateria completa do AGENTS.md.

## Gates para o reviewer (2 linhas)

1. Escopo por criador intacto: nenhum filtro/query novo além do vigente;
   identificação é apenas leitura dos campos no item (sem nova fonte de
   dados).
2. Painel (apps/dashboard) intocado — `git status` não mostra nada de
   dashboard (zero-PHI preservado).

## Out of scope

- Detalhe do NIR (trilha é o item 4 do backlog); filas médico/agendador
  (slice 001); dashboard.
