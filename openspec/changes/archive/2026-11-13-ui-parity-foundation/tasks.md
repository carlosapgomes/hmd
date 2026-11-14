# Tasks: ui-parity-foundation

## 1. Rename SAR–HMD

Contract: `slices/slice-001-rename-sar-hmd.md`

- [x] 1.1 Trocar `APP_DISPLAY_NAME` default para `SAR – HMD` em
  `config/settings/base.py`+`test.py`, títulos e manual; RED: teste
  existente que assevera o nome falha; GREEN: valor novo (override
  por env preservado, com teste de default + 1 override); verificar
  com `uv run pytest tests/ apps/accounts/tests/test_auth_flow.py`
- [x] 1.2 Atualizar `manifest.name`, SVGs (sigla + faixa de família +
  variante `-contrast`), `generate_pwa_icons.py` e regenerar PNGs;
  verificar com `--check` + teste de determinismo (bytes idênticos)
- [x] 1.3b `sw.js`: `CACHE_NAME` → v2 + precache dos novos SVGs,
  bypass de PDF/POST preservado; verificar com teste de cache +
  precache = manifest
- [x] 1.3 Varrer docs/`PROJECT_CONTEXT.md`/testes por `HMD — Hemodinâmica`
  e registrar a regra dos 3 tokens e a premissa externa `SAR – CHD`;
  verificar com `uv run ruff check . && uv run ruff format --check .`
  e `grep` sem ocorrências restantes do nome antigo em superfícies

## 2. Harness verify-hmd

Contract: `slices/slice-002-verify-hmd.md`

- [x] 2.1 Criar `.pi/skills/verify-hmd/SKILL.md` (launch/doctor/drive/
  evidence/cleanup) + helpers shell + drive Playwright pinado,
  settings de verificação (banco próprio + checagens fail-closed) +
  backend sintético exclusivo com teste negativo + seed sintético,
  `.gitignore` += `tmp/verification/`; verificar com o launch +
  doctor do próprio skill (exit 0, run isolado, testes negativos
  verdes, banco de dev intocado)
- [x] 2.2 Mapear 3 features (`features/`: login, home por papel, fila
  médica em leitura) e provar 1 jornada end-to-end com evidência
  (snapshots + PNG + JSON) sobrevivendo ao cleanup; verificar com a
  evidência no diretório nomeado pela skill após o teardown
- [x] 2.3 Registrar no `PROJECT_CONTEXT.md` que o harness é prova
  oficial dos slices visuais (fora do gate); verificar com o gate
  completo verde (`ruff + format + mypy + pytest`) sem tocar `.pi`

## 3. Fundação CSS

Contract: `slices/slice-003-css-foundation.md`

- [x] 3.1 Adotar fontes (Merriweather Sans + Source Sans 3) e tokens
  `--hospital-*` (primária HMD preservada), `.page-title`,
  botões/badges/status/`waiting-time`, touch ≥44px, responsivo;
  RED: testes de HTML asserem classes nos templates-alvo e falham;
  GREEN: CSS serve os seletores; verificar com `pytest` dos testes
  novos + capturas verify-hmd lado a lado com o ats-web
- [x] 3.2 Aplicar `.page-title` + `h1` único nas 17 páginas listadas
  no contrato (filas, detalhes, envio, meus casos, painel, sessão);
  verificar com testes de presença de `h1.page-title` por rota +
  drive do harness (fonte computada, controles ≥44px, viewports
  360px/1280px) + gate verde

## 4. Header, nav e htmx

Contract: `slices/slice-004-header-nav.md`

- [x] 4.1 Reescrever `base.html`: ícone 32px + `SAR – HMD`, faixa
  `app-nav` via `{% block nav %}`, sessão `Nome · Papel` + avatar +
  sino + `?` manual + sair + toggler, favicon/apple-touch, htmx 2.0.4;
  RED→GREEN com testes de marcadores por papel; verificar com
  `pytest` + captura do header nos 4 papéis
- [x] 4.2 Criar `_nav.html` por papel (NIR/médico/agendador/painel)
  com abas no vocabulário ats-web (`Decididos`, sem recorte — débito
  change 2) e contadores, mantendo os `?tab=` atuais; tabela de
  mapeamento no relatório; plugar `{% block nav %}` nas páginas;
  verificar com testes de aba ativa por rota + capturas
- [x] 4.3 Sino com polling vanilla sobre `unread-count` (45 s,
  backoff 5 min, pausa oculta — molde ats-web); RED: teste de
  endpoint + presença do `data-*`; verificar com `pytest` + jornada
  zero→N sem reload, desktop e mobile (extensão limitada do mapa)

## 5. Páginas de sessão

Contract: `slices/slice-005-session-pages.md`

- [x] 5.1 Login no padrão (toggle de senha, aviso transitório mantido,
  sem reset); perfil; verificar com testes de render + captura
- [x] 5.2 Notificações: lista no padrão (referência do caso, leitura
  individual + marcar-todas, voltar) e polling; endpoint de leitura
  individual (reaproveitar `notifications_open` ou POST mínimo);
  RED→GREEN com testes de leitura/acesso/404; verificar com `pytest`
  + jornada (3 não lidas → marca 1 sem abrir → badge 3→2)
- [x] 5.3 Manual: restyle + rename + imprimível, texto instrucional
  preservado (diff de texto); verificar com teste de seções por
  papel + captura
- [x] 5.4 Integração do change: rodar o mapa verify-hmd completo nas
  telas do change, gate completo verde e `PROJECT_CONTEXT.md`
  atualizado; verificar com evidência do harness + gate
