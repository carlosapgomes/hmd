# Design: ui-parity-foundation

## Context

Ver proposal.md (Why). Estado atual levantado por inspeção direta dos
dois repos (somente leitura):

- `templates/base.html` (HMD): marca só-texto, links de papel soltos na
  barra, sessão `display_name` + `badge-role`, sino SSR sem polling, sem
  `{% block nav %}` consumido, sem favicon, sem fontes, sem htmx.
- `static/css/app.css` (HMD, ~150 linhas): tokens `--hmd-*`, sem
  `.page-title`, `.app-nav`, `.case-card`, `.summary-box`, `.status-*`,
  `.upload-zone`, `.exam-type-badge`, `.procedure-combobox`,
  touch-targets ou responsivo detalhado (o ats-web tem ~1000 linhas).
- `apps/accounts/urls.py`: endpoint `notifications/unread-count/` já
  existe — o polling do sino é só frontend.
- `scripts/generate_pwa_icons.py`: fonte única da geometria (texto
  `HMD` centralizado); `--check` espelha o manifest.
- `.gitignore` não ignora `tmp/`; `ruff` já exclui `.pi`.

## Goals / Non-Goals

- Goals: casca indistinguível do ats-web exceto identidade reservada;
  harness de verificação operante; base para os changes 2 e 3.
- Non-Goals: comportamento de filas/upload/decisão/painel (changes 2–3);
  métricas do painel; rename do ats-web; e-mails/reset de senha.

## Decisions

- **D1 — Identidade reservada = 3 tokens.** Primária `#0d4a8a`
  (header, `theme-color`, botões), `short_name "HMD"` (2), sigla
  `HMD` no ícone (3). Todo o resto (secundária do gradiente? NÃO — mantida a atual
  `#16647a` para churn zero no header; fontes, espaçamentos,
  componentes) converge ao ats-web. Alternativa (alinhar até a matiz)
  rejeitada: eliminaria o único sinal ambiental de "sistema errado".
- **D2 — Nomes de tokens CSS convergem.** `app.css` adota os nomes
  `--hospital-*` do ats-web com os valores HMD onde reservado. O
  arquivo vira difável contra o ats-web (facilita reviews futuros de
  deriva). Alternativa (manter `--hmd-*`) rejeitada: dois vocabulários
  para a mesma coisa.
- **D3 — Adaptação, não cópia.** JS/CSS são reescritos sobre os moldes
  do ats-web com os nomes de rotas/fields do HMD (`documents`,
  `attachments`, `?tab=aguardando`, `notifications_open` etc.).
  Nenhum arquivo é copiado byte a byte (AGENTS.md §8).
- **D4 — Parâmetros `?tab=` internos preservados.** Só os rótulos
  visíveis mudam para o vocabulário ats-web — com uma exceção
deliberada: a aba médica chama-se `Decididos` (fiel ao filtro por
estado atual), não `Decididos Hoje` (que no ats-web implica recorte
de data e aqui mentiria). URLs, views e testes de fila seguem
intactos (filas reais vêm no change 2). **Débito registrado para o
change 2 (`ui-parity-queues`):** decidir a semântica da aba de
decididos (adotar o recorte `Decididos Hoje` do ats-web com filtro
de data, ou manter por estado com o rótulo atual) — o slice 004 NÃO
implementa recorte de data.
- **D5 — htmx 2.0.4 via unpkg (mesma do ats-web), mas nada crítico**
  depende dele neste change: o polling do sino é JS vanilla sobre o
  endpoint existente (45 s default, backoff exponencial com teto de
  5 min, pausa em aba oculta, poll imediato ao voltar — mesmo padrão
  do `notifications.js` do ats-web); htmx aqui só entra no `base.html`
  como fundação para os partials do change 2. Degradação graciosa se
  o CDN falhar. Chrome MCP é apoio de diagnóstico, nunca prova.
- **D6 — Ícone: sigla + faixa de família.** `HMD` grande sobre fundo
  da primária + faixa geométrica superior (marca SAR, sem texto
  pequeno ilegível); variante `-contrast` para superfícies escuras
  (fecha o P2 do change 11). Script gerador atualizado (texto + faixa);
  `--check` continua porta de PNGs commitados.
- **D7 — verify-hmd em shell + Node `.mjs` (única exceção Python: o
  seed).** Escapa de
  `ruff` (`.pi` excluído) e `mypy strict`; Playwright pinado local
  (molde `verify-sar`); banco próprio `hmd_verify_*` via settings de
  verificação; seed por management command com fixtures sintéticas;
  login real pelo form (cookie injetado nunca é prova); evidência em
  `tmp/verification/` (+ linha no `.gitignore`). Mapa inicial: login,
  home por papel, fila médica em leitura; 1 jornada provada (§4 do
  skill gerador). Fora do quality gate.
- **D7b — Autenticação da verificação (aprovada pelo dono).** O
  `LocalAccountBackend` só aceita superusuário sem `ad_upn`; logo, o
  seed sozinho não autentica usuários de papel. `verify.py` configura
  um backend de validação sintética **exclusivo de verificação**
  (módulo próprio, habilitado só nesse settings, que recusa carregar
  sob qualquer outro settings — com teste negativo). O teste negativo
  + a checagem fail-closed do banco provam que dev/prod nunca usam
esse caminho.
- **D8 — Manual segue estático e autorado no HMD** (não migra para
  renderização backend como o ats-web): só restyle + rename. Conteúdo
  e cobertura inalterados → sem delta de requisito além de `pwa-manual`
  (identidade).
- **D9 — Login mantém o aviso de acesso transitório** e não ganha
  "esqueci a senha" (sem e-mails: fora de escopo permanente, ADR-0003).

## Risks / Trade-offs

- [Risco] Google Fonts/htmx bloqueados num segmento da intranet →
  Mitigação: ats-web já carrega ambos no hospital (fato observado);
  fallback de pilha nativa no CSS; polling do sino em vanilla JS.
- [Risco] Deriva futura HMD×ats-web → Mitigação: tokens com mesmos
  nomes (D2) + screenshots do harness por slice; `PROJECT_CONTEXT.md`
  registra a regra dos 3 tokens.
- [Risco] Chromium do Playwright sem rede para download →
  Mitigação: tarefa do slice 002 detecta cedo e reporta BLOCKED com
evidência (sem trocar de motor em silêncio); Chrome MCP, já
configurado pelo dono, serve só como diagnóstico.
- [Risco] Rename quebra links/docs externos → Mitigação: slice 001
  varre `APP_DISPLAY_NAME`, manifest, testes, docs e manual; sem
  redirect (mesmas rotas).

## Migration Plan

Sem migrations. Deploy: estáticos + service worker (`CACHE_NAME`
`hmd-cache-v1` → `hmd-cache-v2` + precache incluindo os novos SVGs —
`sw.js` entra no slice 001, preservando o bypass de PDF/POST) +
settings. Rollback: revert do change (nenhum dado migra). O
`verify-hmd` nunca toca banco de dev/prod (settings próprias +
checagens fail-closed).
