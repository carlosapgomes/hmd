# Slice 002 — Harness verify-hmd

```yaml
expected_files:
  - .pi/skills/verify-hmd/SKILL.md
  - .pi/skills/verify-hmd/scripts/* (launch/doctor/drive/cleanup, shell + .mjs)
  - .pi/skills/verify-hmd/features/README.md + 3 features
  - config/settings/verify.py (novo, banco hmd_verify_* + backend
    sintético exclusivo + checagens fail-closed de host/nome)
  - apps/accounts/backends_verify.py (novo, validação sintética só
    de verificação; recusa carregar sob outro settings)
  - apps/accounts/management/commands/seed_verify.py (novo, fixtures sintéticas)
  - .gitignore (tmp/verification/)
  - PROJECT_CONTEXT.md (nota do harness)
allowed_incidental_files: []
out_of_scope:
  - qualquer template/CSS/JS do app (slices 003-005)
  - capturas das telas pós-paridade (slices seguintes estendem o mapa)
  - gate/CI (harness fica fora do quality gate)
```

## Objetivo

Skill `verify-hmd` operante (molde `create-verification-skill` +
`verify-sar`, adaptado ao Django): instância isolada, drive Playwright
pinado como prova oficial e 1 jornada provada com evidência.

## Contexto necessário

- Skill geradora: `/home/dev/.pi/agent/skills/create-verification-skill/SKILL.md`
  (§1 entrevista do repo, §2 SKILL.md + launch/doctor/drive/evidence/
  cleanup/helpers, §3 mapa inicial 3–5 features, §4 provar 1 jornada).
- Referência viva: `/projects/dev/sarv3/.pi/skills/verify-sar/`
  (launch→doctor→drive→evidence→cleanup, prova via form real, cookie
  injetado nunca é prova, evidência em `tmp/verification/`).
- HMD: login local transitório (sem AD nesta fase — seed cria
  superusuário local + usuários por papel); test settings usam banco
  dedicado (`config/settings/test.py`); `.pi` excluído do ruff
  (`pyproject.toml`); `config/` e comandos cobertos pelo mypy strict
  (helpers do harness em shell/Node, zero Python fora do seed).
- Decisões do design: D7 (shell + `.mjs`, Playwright pinado, banco
  próprio, login real pelo form, fora do gate).

## Requisitos verificáveis

- **R1** — Launch sobe o app com settings de verificação (porta de
  loopback própria, banco `hmd_verify_*` migrado com checagens
  fail-closed de host/nome, seed sintético: usuários
  nir/doctor/scheduler/manager/admin + casos) e o doctor read-only
  atesta propriedade (PID/build/origem/marcador) e recusa instância
  alheia.
- **R1b** — Autenticação da verificação (aprovada pelo dono): o
  backend sintético autentica os usuários de papel pelo form real e
  recusa carregar sob qualquer outro settings (teste negativo); um
  segundo teste negativo prova que a config de verificação nunca
  aponta para banco prod-like; nenhum cookie injetado é usado.
- **R2** — Drive Playwright executa login real pelo form, home por
  papel e leitura da fila médica; seletores vindos de snapshots reais.
- **R3** — 1 jornada provada end-to-end com evidência (snapshots +
  PNG + JSON + observed-state) em `tmp/verification/<run>/`,
  sobrevivendo ao cleanup; mapa inicial com 3 features nos 4 Hs
  exigidos (sub-features, como chegar, como dirigir, gotchas);
  SKILL.md com frontmatter válido e helpers executáveis invocados no
  corpo; cleanup também após iterações falhas.
- **R4** — Gate completo verde (seed e backend tipados, cobertos pelo
  strict; `.pi` fora do lint).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R1b | `verify.py`, `backends_verify.py`, seed, scripts | launch + doctor exit 0; testes negativos (outro settings, banco prod-like); banco de dev intocado |
| R2 | drive `.mjs` | jornada passa contra a instância owned |
| R3 | `features/*`, `tmp/verification/` | evidência presente após cleanup |
| R4 | `seed_verify.py` | `mypy` + gate completo exit 0 |

## Plano de testes (failing-before do skill gerador)

### RED

- Sem a skill, `.pi/skills/verify-hmd/scripts/control-hmd launch`
  falha (comando inexistente); doctor contra instância alheia deve
  recusar (`DO NOT drive`).

### GREEN / verificação local

- launch → doctor → drive (1 jornada) → cleanup — exit 0 em cada etapa
- evidência verificada em disco após o teardown
- `uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest` — exit 0

## Critérios de aceitação

- [ ] R1–R4 verdes (inclui R1b); run isolado nunca toca banco de dev/prod
- [ ] Nenhuma prova usa cookie injetado, TestClient como browser ou
  rota de mutação de teste; Chrome MCP, se usado, só como diagnóstico
- [ ] Se o Chromium não baixar (sem rede), reportar BLOCKED com a
  evidência e o fallback Chrome MCP indicado — não trocar de motor
  em silêncio

## Relatório de evidência

`/tmp/hmd-slice-002-report.md` (`READY_FOR_REVIEW` ou
`BLOCKED_NEEDS_DECISION`, incluindo o run id e o caminho da evidência).

## Handoff

Implementar apenas este slice. Escalar se a entrevista do repo (§1 do
skill gerador) revelar premissa falsa (ex.: login local indisponível,
porta/banco exigidos em uso) ou se for preciso persistência nova no
app. Não iniciar o slice 003.
