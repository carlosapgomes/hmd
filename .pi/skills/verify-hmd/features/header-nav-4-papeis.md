# Header, navegação e sino — 4 papéis (slice 004 + OLI-005)

Prova visual do header padrão ats-web com faixa por papel e sino com
polling (ui-parity-foundation slice 004), sobre a instância isolada do
`verify-hmd`. Segue os mesmos padrões de prova do skill: login real pelo
form (nunca cookie injetado), Chromium pinado, evidência em
`tmp/verification/<run>/artifacts/`.

OLI-005 (`operational-lists-ui-parity`, D11) adiciona a prova das pills
operacionais e do mecanismo único de contador: aba ativa com fundo
branco + texto `--hospital-primary` (`#0d4a8a`), hover/focus visíveis,
contadores só por `data-count` + pseudo-elemento `::after` (pendentes
médico/agendador em ação `--hospital-danger`; demais neutros
`--hospital-muted`; zero oculto por CSS), sem badge `<span>` inline e
sem contagem duplicada no DOM. Nenhuma captura sem `.app-shell` com
opacity computada ≥ 0.99 (sem sleep fixo).

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
- `oli005-nir-my-cases`: `verify-nir` → `/intake/cases/?tab=active&q=VERIFY&status=FAILED`
  — *Meus Casos* neutro não-zero (`::after` "2"), *Casos Encerrados*
  zero oculto, `filter_qs` preservado nos dois links e `aria-current`.
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
(mobile) e `bell-polling.json` + `report-header-nav-4-papeis.json`
(relatório em nome exclusivo — `report.json` pertence ao drive genérico
`drive.mjs`; os dois drives podem rodar na mesma run sem sobrescrita).

## Notas

- A contagem do sino é lida do `data-count` dos dois sinos (mobile +
  desktop) e do `aria-label`; `htmx 2.0.4` é só fundação (tag no
  `base.html`) — o sino usa `static/js/bell_poll.js` vanilla.
- A criação da notificação de teste é a única escrita fora do form e
  vive nesta journey, sobre o banco `hmd_verify_*` descartável —
  nunca em dev/prod.
- OLI-005: o drive grava também `oli005-nav.json` — computed styles da
  aba ativa (fundo branco, texto `rgb(13,74,138)`), hover
  (`rgba(255,255,255,0.12)`/branco), focus por teclado (outline
  `rgb(212,238,243)`) e `::after` dos contadores (ação
  `rgb(181,53,53)`, neutro `rgb(76,102,119)`, zero `display:none`),
  texto dos links sem dígitos (contagem única), alvos ≥44px, wrap em
  390px sem overflow e estabilidade em duplo rAF.
