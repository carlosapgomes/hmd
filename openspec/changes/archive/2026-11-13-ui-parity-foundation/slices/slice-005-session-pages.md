# Slice 005 — Páginas de sessão no padrão

```yaml
expected_files:
  - templates/accounts/login.html
  - templates/accounts/profile.html
  - templates/accounts/notifications.html
  - templates/accounts/manual.html (só marca/estilo; conteúdo inalterado)
  - static/js/password_toggle.js (novo, molde ats-web adaptado)
  - apps/accounts/views.py + urls.py (leitura individual, se não reaproveitável via notifications_open)
  - apps/accounts/tests/test_notifications*.py (novos/estendidos, tipados)
  - .pi/skills/verify-hmd/{features,scripts}/ (extensão limitada: jornadas notificacoes e sessoes)
  - PROJECT_CONTEXT.md (fecho do change)
allowed_incidental_files: []
out_of_scope:
  - reset de senha/e-mails (fora de escopo permanente, ADR-0003)
  - conteúdo do manual (D8: só restyle + rename)
  - changes 2–3
```

## Objetivo

Login (com toggle), perfil, notificações (leitura individual +
polling) e manual no padrão visual da casca, fechando o change.

## Contexto necessário

- Molde login ats-web (`toggle-password` + `input-group`; sem o link
  de reset — D9) e `password-toggle.js`; HMD mantém o aviso de acesso
  transitório.
- `apps/accounts/views.py`: `notifications_open` (marca+lida e
  redireciona), `notifications_mark_all_read`, `unread-count`;
  leitura individual reaproveita `notifications_open` se o método
  suportar POST sem redirect — senão POST mínimo novo com guard de
  destinatário (404 alheio, como no spec).
- Specs deste change: `notifications` (polling + leitura individual),
  `pwa-manual` (manual existe; visual pela `ui-shell`).

## Requisitos verificáveis

- **R1** — Login com toggle de senha funcional, aviso transitório,
  sem link de reset.
- **R2** — Lista de notificações com referência do caso, leitura
  individual por item, marcar-todas, voltar e polling; acesso
  restrito ao destinatário (404 + não marca). Jornada do harness:
  3 não lidas → marca 1 sem abrir → só ela muda + badge 3→2;
  item alheio retorna 404.
- **R3** — Perfil e manual no padrão (page-title, cards); manual
  imprimível com o texto instrucional preservado (evidência = diff
  de texto extraído: só marca/marcaup mudam; conteúdo das seções
  por papel inalterado).
- **R4** — Mapa verify-hmd executado nas telas do change;
  `PROJECT_CONTEXT.md` atualizado com o fecho.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `login.html`, `password_toggle.js` | teste de render + marca do toggle |
| R2 | `notifications.html`, views/urls | testes de leitura individual/404/polling + jornada harness |
| R3 | `profile.html`, `manual.html` | testes de seções + captura |
| R4 | mapa + `PROJECT_CONTEXT.md` | evidência do harness + gate |

## Plano de testes

### RED

- Comando: `uv run pytest apps/accounts/tests/ -q` (com os testes
  novos de leitura individual e toggle antes da implementação)
- Falha esperada: rota/marcador inexistente (404 no teste ou HTML
  sem o controle).

### GREEN / verificação local

- `uv run pytest -q`, `mypy`, `ruff check/format` — exit 0
- jornada verify-hmd fim a fim: badge → lista → abrir → badge zerado

## Critérios de aceitação

- [ ] R1–R4 verdes; nenhum e-mail/reset introduzido
- [ ] Texto instrucional do manual preservado (diff de texto só com marca/markup)
- [ ] Gate completo verde + evidência do harness anexada ao relatório

## Relatório de evidência

`/tmp/hmd-slice-005-report.md` (requirements→evidence, arquivos,
failing-before/passing-after, comandos com exit status, capturas e
jornada do harness).

## Handoff

Implementar apenas este slice. Escalar se a leitura individual exigir
modelagem além de um POST mínimo, ou se qualquer página de sessão
exigir dado novo de backend. Não commitar, arquivar ou iniciar o
change 2 — apenas `READY_FOR_REVIEW`.
