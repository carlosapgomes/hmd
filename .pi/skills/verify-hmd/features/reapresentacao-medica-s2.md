# Reapresentação médica S2 (slice 002)

Substituição médica com espera preservada: original pendente → novo
processado pelo orquestrador real → original histórico vinculado →
um card novo com espera herdada.

## Pré-condição (setup real, nunca prova)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
# sob a identidade da run (db/media do manifest):
VERIFY_DB_NAME=<manifest.database> VERIFY_DB_PORT=<manifest.dbPort> \
  HMD_VERIFY_MEDIA_ROOT=<run>/media \
  uv run python manage.py seed_verify --reapresentacao-s2 --settings=config.settings.verify
```

Setup cria pela primitiva e pipeline reais o par `880101` (mesmo nome +
nº, `angio_art_perif` com concordância no `config.llm_verify`): original
envelhecido 6 dias após concluir e reenvio processado pelo orquestrador
real, aplicando a substituição S2 no atomic final. Repetir preserva a
cadeia aplicada; estado parcial incompatível aborta explícito.

## Jornadas (via entrypoint oficial)

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd drive --run <tmp/verification/hmd-verify-...> --journey s2
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

Todo drive passa por `control-hmd drive` (doctor + Chromium pinado +
`drive.mjs --journey s2`); invocação direta do driver customizado não é
prova. Mesma posse/socket/build/static do default, sem ownership duplicado.

- `s2-doctor-queue` (1280+390): fila médica com UM card `880101` e espera
  herdada `6 dias` (`.waiting-time`), `static/css/app.css` + `static/js/sw.js`
  200 e nenhum `/static/* >= 400`.
- `s2-doctor-detail` (1280+390): href do card → detalhe read-only do
  representante com `880101`.
- `s2-nir-history`: busca `closed-cases/?q=880101` → histórico com card
  `Reapresentação do mesmo pedido`, elos `Substituído por` e
  `Representante atual` coincidentes (single-hop runtime; cadeia A→B→C
  provada pelo HTTP de `test_reappearance.py`), representante ativo em
  `/intake/cases/` com `880101`.
- Unidade D (recuperação observável, `s2-d-*`): fixture pendente `880102`
  criada NO DRIVE via `runVerifyPy` pelo serviço real (lease viva →
  PENDING com agenda; seed intocado) → avisos `Recuperação de reapresentação
  agendada` nos detalhes NIR (`/intake/cases/<incoming>/`) e gerencial
  (`/dashboard/case/<incoming>/`) do incoming, ausência de qualquer aviso no
  prior e ausência de botão de reenqueue; estado `schedule_failed` sem
  agenda (setup honesto) → aviso de falha + suporte; comando oficial
  `reenqueue_pending_reappearances <id>` (SIMULAÇÃO), `--apply` (AGENDADO)
  e repetição (NO-OP) em subprocesso contra o banco isolado → aviso
  regularizado para agendada com histórico do ciclo (falha + reenfileirada)
  preservado na trilha. Gates: GET read-only, sem terceira resposta NIR.

Evidência em `<run>/artifacts/` (`report-s2.json`,
`s2-*.png/txt`, `observed-state-s2.json`), sem sobrescrever
`report.json` do default. Exit `0` PASS · `1` falha observada · `2`
infra/driver (Chromium pinado indisponível = **BLOCKED**).
