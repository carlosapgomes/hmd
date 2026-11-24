# HMD verification feature map

Maintained verification source for the `verify-hmd` skill. Start with the
matching entry point; an alternate successful direct URL never proves a
failed original path. The map covers the **initial** surface only (login,
per-role home, medical-queue reading) — later slices extend it.

## Baseline preconditions

- Launch with `.pi/skills/verify-hmd/scripts/control-hmd launch`, retain its
  exact run directory, then run `doctor --run` against that directory
  (exit 0 required before driving).
- The real Django app runs via `manage.py runserver` on loopback under
  `config.settings.verify`, with a disposable `hmd_verify_*` database and
  the synthetic seed.
- Seeded identities (public, fictional): `verify-nir`, `verify-doctor`,
  `verify-scheduler`, `verify-manager`, `verify-admin` — password
  `hmd-verificacao-ficticia-2026` for all. Two synthetic cases
  (`VERIFY-001`, `VERIFY-002`) sit in the medical queue.
- Each drive uses its own isolated Playwright browser context; never the
  operator's browser profile.

## Driving conventions

Real form fills and button/link clicks only. No injected cookies, no direct
authenticated HTTP, no internal state setters, no fabricated screenshots.
Database inspection, if any, is read-only observation after UI actions —
never a mutation to make a feature pass.

## Proof and skip reporting

Capture action + before/after snapshots/screenshots + resulting URL. Reports
distinguish PASS, FAIL and driver infrastructure errors (exit 2). Initial
seeding is never feature proof. Skipped entry points remain unverified.

## Features

- [Login](login.md): the real login form at `/login/` — synthetic role user
  authenticates, lands on the role home, and the session survives in the
  browser context created by the form POST.
- [Home por papel](home-por-papel.md): each single-role account lands on its
  work area after login — NIR intake, medical queue, scheduler queue,
  manager/admin panel — with the expected heading.
- [Fila médica em leitura](fila-medica.md): the doctor queue lists the
  synthetic cases as cards (`VERIFY-001`, patient name, declared procedures)
  and the first card opens the case work page read-only, without submitting
  any decision.
- [Fila médica — decididos orientados ao resultado](fila-medica-decididos.md): the
  decided tab (`Casos decididos`) with per-procedure disposition/time cards,
  mixed approved+denied without case-level aggregation, `Ver detalhes` CTA
  to the read-only detail, filter without counts, empty `alert-info` and
  1280/390px measures (operational-lists-ui-parity slice 003).
- [Header, navegação e sino — 4 papéis](header-nav-4-papeis.md): header
  padrão ats-web por papel com faixa de navegação, toggler mobile e sino
  com polling zero→1 sem reload (slice 004).
- [Páginas de sessão](sessoes.md): login com toggle de senha (clique real
  troca o `type` do input, aviso transitório, sem reset), perfil e manual
  no padrão (slice 005).
- [Notificações](notificacoes.md): jornada 3 não lidas → marca 1 sem abrir
  → sino 3→2 → abrir caso → marcar todas → sino zerado; item alheio 404
  (slice 005).
- [Upload com drag-and-drop](upload-dragdrop.md): zona + preview (lista,
  contagem, tamanho), toggle de anexos 1→2→1, submit sem confirmação,
  lote misto nomeado e form nativo sem JS (ui-parity-intake-detail
  slice 001).
- [Combobox de procedimento](combobox-procedimento.md): busca por
  nome/código sem acentos, teclado ↑↓/Enter, edição pós-seleção limpa
  o canônico, submit não-confirmado rejeitado e select nativo sem JS
  (ui-parity-intake-detail slice 002).
- [Detalhe, decisão e confirmação por papel](detalhe-papeis.md): jornada médico→agendador→NIR no layout ats-web (ui-parity-intake-detail slice 003).
- [Painel gerencial no visual](painel-visual.md): toolbar de período, cards de métrica e lista em `case-card` com os mesmos números e filtros (ui-parity-intake-detail slice 004).
- [Revisão NIR do procedimento](revisao-procedimento-nir.md): card declarado×detectado com select único dos 13 tipos, manutenção e troca para não detectado com reprocessamento real, gating, teclado, viewports 390/1280, select sem JS e zero transporte externo (confirm-nir-procedure-review slice 002).
- [Reapresentação médica S2](reapresentacao-medica-s2.md): original pendente → novo processado pelo orquestrador real → histórico vinculado → um card novo com espera herdada 6 dias; drive oficial `control-hmd drive --run <dir> --journey s2` em 1280+390 (reapresentacao-casos-filas slice 002).
- [Reapresentação no agendamento S4](reapresentacao-agendamento-s4.md): fila com contador/último recebimento/espera herdada, `#reapresentacoes-recebidas` com lazy PDF desktop + viewer mobile + imagem, histórico retido vs purgado e confirmação real sem triagem clínica; drive oficial `control-hmd drive --run <dir> --journey s4` em 1280+390 (reapresentacao-casos-filas slice 004).
