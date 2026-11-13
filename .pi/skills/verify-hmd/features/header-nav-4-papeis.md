# Header, navegação e sino — 4 papéis (slice 004)

Prova visual do header padrão ats-web com faixa por papel e sino com
polling (ui-parity-foundation slice 004), sobre a instância isolada do
`verify-hmd`. Segue os mesmos padrões de prova do skill: login real pelo
form (nunca cookie injetado), Chromium pinado, evidência em
`tmp/verification/<run>/artifacts/`.

## Jornadas

- `nir-header-nav`: `verify-nir` → `/intake/` — marca + sessão, faixa com
  *Novo Encaminhamento* (ativa), *Meus Casos* (`?tab=active`) e
  *Casos Encerrados* (`?tab=closed`).
- `doctor-header-nav`: `verify-doctor` → `/doctor/?tab=aguardando` —
  *Pendentes* (ativa) e *Decididos* (`?tab=decididos`, sem "Hoje").
- `scheduler-header-nav`: `verify-scheduler` → `/scheduler/?tab=aguardando`
  — *Pendentes* (ativa) e *Processados* (`?tab=processados`).
- `manager-header-nav`: `verify-manager` → `/dashboard/` — sem faixa
  (painel sem abas próprias); marca + sessão apenas.
- `mobile-toggler`: contexto estreito (390px) — toggler visível, abre a
  sessão sem overflow horizontal; captura estreita por papel.
- `bell-polling-zero-a-um`: na página do médico, a contagem do sino sai
  de 0 para 1 sem reload (poll de 45 s; no mobile, o poll imediato ao
  voltar via `visibilitychange`). A notificação é criada no banco
  isolado pela própria journey (`UserNotification` sintética, sem PHI);
  o sino é o único efeito observado.

## Como dirigir (Playwright)

```bash
node .pi/skills/verify-hmd/scripts/browser/drive-header-nav.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (mesmas amarras do
`drive.mjs`: PID/ticks/boot/marker/build/staticRoot/socket). Cada papel
abre contexto isolado, loga pelo form e captura
`<papel>-header.png/.txt` (desktop), `<papel>-header-mobile.png/.txt`
(mobile) e `bell-polling.json` + `report.json`.

## Notas

- A contagem do sino é lida do `data-count` dos dois sinos (mobile +
  desktop) e do `aria-label`; `htmx 2.0.4` é só fundação (tag no
  `base.html`) — o sino usa `static/js/bell_poll.js` vanilla.
- A criação da notificação de teste é a única escrita fora do form e
  vive nesta journey, sobre o banco `hmd_verify_*` descartável —
  nunca em dev/prod.
