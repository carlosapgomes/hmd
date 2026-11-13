# Páginas de sessão — login com toggle, perfil e manual (slice 005)

Prova das páginas de sessão no padrão (ui-parity-foundation slice 005,
R1/R3), sobre a instância isolada do `verify-hmd`. Mesmo padrão de prova
do skill: login real pelo form, Chromium pinado, evidência em
`tmp/verification/<run>/artifacts/`.

## Jornadas

- `login-password-toggle`: `GET /login/` anônimo mostra o campo *Senha* em
  `input-group` com botão *Mostrar senha* → clique alterna o input para
  `type="text"` e o botão passa a *Ocultar senha* → aviso de acesso
  transitório visível e **nenhum** link de reset/esqueci-senha na página →
  `/static/js/password_toggle.js` servido (HTTP 200).
- `doctor-profile-manual`: `verify-doctor` logado pelo form →
  `/profile/` com `h1` *Meu perfil*, cards de dados/alteração de senha e
  link *← Voltar* → `/manual/` com `h1` *Manual de uso*, seções por papel
  em cards e CSS `@media print` (texto instrucional preservado — diff de
  texto extraído no relatório do slice).

## Como dirigir (Playwright)

```bash
node .pi/skills/verify-hmd/scripts/browser/drive-session-pages.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor --run`). Capturas: `login-before-toggle.png/.txt`,
`doctor-profile.png/.txt`, `doctor-manual.png/.txt`, `sessoes.json` e
`report.json`.

## Notas

- O toggle é JS vanilla (`password_toggle.js`, adaptado do molde
  ats-web) — a prova é o clique real no botão e a troca observada do
  `type` do input, não a presença do arquivo.
- Login em si (form real, destinos por papel) segue provado pela feature
  `login.md`; esta journey só cobre o toggle e as páginas de sessão.
