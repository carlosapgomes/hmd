# Proposal: ui-parity-foundation

## Why

O NIR opera os dois sistemas lado a lado — `ats-web` (CHD) e HMD
(hemodinâmica) — e hoje o HMD parece "outro sistema": sem logo no header,
sem faixa de navegação por papel, sem as fontes do tema hospitalar, com
filas em `list-group` simples e sem as interações (busca, auto-refresh,
drag-and-drop, combobox) que o NIR já usa no CHD. Cada diferença cobra
reaprendizado e aumenta o risco de operar o sistema errado. Este change
cria a fundação da paridade (marca, casca, CSS system, header/nav,
harness de verificação) sobre a qual os changes 2 (filas) e 3
(intake/detalhe/painel) vão construir.

## What Changes

1. **Rename para `SAR – HMD`** (família SAR = apps de apoio à regulação):
   `APP_DISPLAY_NAME`, `manifest.name`, títulos, ícone com sigla `HMD` +
   marcador geométrico de família, variante `-contrast`, PNGs regenerados
   pelo `scripts/generate_pwa_icons.py`. Cor primária `#0d4a8a` e
   `short_name "HMD"` preservados — são a identidade reservada do HMD.
   O rename espelho do ats-web (`SAR – CHD`) é premissa externa, fora
   deste plano.
2. **Harness `verify-hmd`** (`.pi/skills/verify-hmd/`, molde
   `create-verification-skill` + `verify-sar`): launch isolado (settings
   de verificação, banco próprio, seed sintético), doctor read-only,
   drive Playwright pinado (prova oficial; Chrome MCP como apoio de
   diagnóstico), mapa inicial com 3 features (login, home por papel, fila
   médica em leitura) e 1 jornada provada. Evidência em
   `tmp/verification/` (gitignored). Fora do quality gate.
3. **Fundação CSS**: Google Fonts (Merriweather Sans + Source Sans 3,
   mesma do ats-web — sem problema na intranet), tokens hospitalares
   alinhados (primária HMD preservada), `.page-title`, botões/badges/
   status/`waiting-time`, alvos de toque ≥44px, responsivo.
4. **Header padrão ats-web**: ícone 32px + título, faixa `app-nav`
   (`_nav.html` por papel, abas `Pendentes/Decididos`,
   `Novo Encaminhamento/Meus Casos/Casos Encerrados`), sessão
   `Nome · Papel`, sino com polling de 45 s no endpoint `unread-count`
   existente, botão `?` de manual, toggler mobile, `htmx 2.0.4` (mesma
   versão do ats-web), favicon/apple-touch-icon. `Decididos` é fiel à
   query atual (sem recorte de data); a semântica `Decididos Hoje` do
   ats-web fica como débito do change 2.
5. **Páginas de sessão no padrão**: login (toggle de senha), perfil,
   notificações (leitura individual + polling) e manual.

## Capabilities

### New Capabilities

- `ui-shell`: casca da aplicação — marca/identidade reservada, header com
  faixa de navegação por papel, padrão de título de página, fundação
  visual (fontes/tokens/componentes) e interações transversais (htmx,
  sino com polling). É a fronteira que os changes 2 e 3 vão estender.

### Modified Capabilities

- `pwa-manual`: identidade PWA `HMD` → `SAR – HMD` (manifest, ícone com
  marcador de família + variante `-contrast`); manual mantém requisito,
  ganha o padrão visual da casca.
- `notifications`: sino passa a atualizar a contagem via polling
  (endpoint existente) e a lista ganha leitura individual por
  notificação (além do marcar-todas).

## Impact

- **Código**: `config/settings/{base,test}.py`, `static/css/app.css`,
  `static/js/{notifications,badge}.js` (novos, moldes ats-web adaptados),
  `templates/base.html` + `_nav.html` por papel + páginas de sessão,
  `static/icons/*` + `scripts/generate_pwa_icons.py`, `.pi/skills/
  verify-hmd/` (fora do gate: `.pi` já excluído do ruff), `.gitignore`
  (`tmp/verification/`), `templates/accounts/notifications.html`
  (ação marcar-uma) + `apps/accounts/views.py` (endpoint já existe para
  abrir; leitura individual reaproveita ou adiciona POST mínimo).
- **Specs**: `ui-shell` (nova), `pwa-manual` (MODIFIED identidade),
  `notifications` (MODIFIED polling + leitura individual).
- **Dependências novas**: `htmx 2.0.4` via CDN (mesma do ats-web);
  Playwright pinado só na skill (não no app).
- **Fora de escopo** (changes 2–3): filas, meus casos, upload, combobox,
  detalhe/decisão, painel.
- **Comportamento preservado**: rotas, guards, FSM, política consultiva,
  anonimização fail-closed, tabs internas (`?tab=`) — paridade aqui é
  visual + interações transversais, não regra de negócio.
