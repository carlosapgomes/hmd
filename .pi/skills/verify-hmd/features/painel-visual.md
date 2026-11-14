# Painel gerencial no visual (ui-parity-intake-detail slice 004)

Prova do painel no padrão ats-web (ui-parity-intake-detail, slice 004,
R1–R3): toolbar de período, cards de métrica e lista em `case-card`
com os mesmos números e filtros de antes — só classes e estrutura
visual mudam (queries, filtros e fontes imutáveis intactos — paridade
funcional de métricas é change futuro). Sobre a instância isolada do
`verify-hmd`, com os casos sintéticos `VERIFY-001`/`VERIFY-002`.

## Jornadas

- `manager-painel`: `verify-manager` logado pelo form → `/dashboard/`
  com `.metrics-period-card` + `#metrics-period-selector` (4 opções
  Hoje/7 dias/30 dias/Tudo, ativa no default `hoje`), 6
  `.metrics-summary-card` (Total/Agendados/Negados/Em andamento/
  Encerrados/Encerr. administrativos), tabela *Procedimentos por
  tipo*, *Agendamentos por unidade*, *Tempo médio* e 2 `.case-card`
  (`VERIFY-001`/`VERIFY-002` com pacientes sintéticos e link de
  detalhes).
- `manager-painel-7d`: clique em *7 dias* na toolbar → URL
  `?period=7d` com `is-active` no *7 dias*; os mesmos 2 cards.
- `manager-painel-filtered`: busca `VERIFY-001` no campo *Busca* +
  *Filtrar* → 1 card (server-side, AND com os demais filtros);
  `VERIFY-002` some. Navegação nova para `/dashboard/` restaura os 2.
- `admin-painel`: `verify-admin` logado pelo form → mesma superfície
  do painel (mesma rota por papel ativo manager/admin).

## Como dirigir (Playwright)

```bash
TEST_DB_PORT=55435 .pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-painel-visual.mjs --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor`). Capturas: `manager-painel`,
`manager-painel-7d`, `manager-painel-filtered`, `admin-painel`
(+ `report.json`/`observed-state.json`).

## Gotchas

- Os casos sintéticos `VERIFY-001`/`VERIFY-002` (`AWAITING_DOCTOR`)
  são setup, nunca prova de métrica: os números exatos do período
  ficam provados pelo pytest (`test_panel_visual.py` — valores
  renderizados = `compute_summary`), e a suíte de métricas existente
  passa sem alteração (prova R2).
- A toolbar preserva os filtros da lista ao trocar o período
  (`list_qs`) — o caminho de filtro é provado após o clique em
  *7 dias*, com busca + período vigentes juntos.
- Cleanup destrói o banco inteiro, sem vazamento entre runs.
