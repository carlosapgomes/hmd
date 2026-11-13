# Notificações — lista, leitura individual e sino (slice 005)

Prova das páginas de sessão no padrão (ui-parity-foundation slice 005,
R2), sobre a instância isolada do `verify-hmd`. Segue os mesmos padrões
de prova do skill: login real pelo form (nunca cookie injetado), Chromium
pinado, evidência em `tmp/verification/<run>/artifacts/`.

## Jornada

- `notifications-3-to-2-to-0` (`verify-doctor`): 3 notificações sintéticas
  não lidas criadas no banco isolado pela própria journey (`UserNotification`
  sem PHI, sobre os casos `VERIFY-001`/`VERIFY-002`) → sino exibe 3 →
  `/notifications/` lista os 3 itens com a referência do caso
  (`Caso VERIFY-001`/`VERIFY-002`), formulário *Abrir caso* e formulário
  *Marcar como lida* por item não lido → clica **Marcar como lida** no
  primeiro item (sem abrir o caso) → volta à lista, só ela muda e o sino
  cai para 2 → clica **Abrir caso** em outro item → aterrissa no detalhe
  do caso na visão do médico e o sino cai para 1 → **Marcar todas como
  lidas** → sino zerado.
- `notifications-foreign-404`: a journey cria 1 notificação para
  `verify-nir` e, como `verify-doctor`, faz POST (com CSRF da página) à
  rota de leitura individual dela → HTTP 404 e nada é marcado (a lista
  do NIR não muda — observado via contagem no banco isolado).

## Como dirigir (Playwright)

```bash
node .pi/skills/verify-hmd/scripts/browser/drive-session-pages.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor --run`; mesmas amarras do `drive-header-nav.mjs`:
PID/ticks/boot/marker/build/staticRoot/socket). Capturas:
`doctor-notifications{,-after-mark1,-after-open,-after-markall}.png/.txt`,
`notifications.json` e `report.json`.

## Notas

- A criação das notificações de teste é a única escrita fora do form e
  vive nesta journey, sobre o banco `hmd_verify_*` descartável — nunca em
  dev/prod (mesmo padrão do sino do slice 004).
- O polling do sino (`bell_poll.js` sobre `unread-count`, 45 s) já foi
  provado no slice 004; aqui o sino é observado via SSR pós-POST e o
  marcador de polling (`data-unread-count-url`) é asserido na lista.
- O 404 alheio também tem cobertura pytest
  (`test_notifications_session_pages.py`); aqui é a prova em browser
  real do mesmo contrato.
