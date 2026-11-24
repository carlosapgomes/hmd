# Conferência NIR de reapresentação S5 (slice 005)

Conferência de reapresentação pelo NIR não-criador em escopo de equipe:
comparativo lado a lado (`#conferencia-reapresentacao`) com exatamente 2 botões
de resposta, visualização de PDFs (lazy desktop e viewer mobile com `Voltar`),
silêncio sem bloqueio, pedido independente, substituição na fila médica,
repetição de agendamento e negativa histórica tardia pós-LLM2.

## Pré-condição (setup real, nunca prova)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
# sob a identidade da run (db/media do manifest):
VERIFY_DB_NAME=<manifest.database> VERIFY_DB_PORT=<manifest.dbPort> \
  HMD_VERIFY_MEDIA_ROOT=<run>/media \
  uv run python manage.py seed_verify --reapresentacao-s5 --settings=config.settings.verify
```

Setup cria o usuário criador sintético `verify-nir-criador` (para que
`verify-nir` atue como outro NIR não-criador da equipe) e 4 pares fracos por
`origin_unit` (mesmo `patient_name` + `procedure_type` + `origin_unit`,
`agency_record_number` distintos e CNS vazio — nunca auto-resolvem):
- **Par 1 (Silêncio + Independente):** `880501` (`AWAITING_DOCTOR`) + `880502`
  (`AWAITING_DOCTOR`), origem `Unidade Origem S5 Alfa`.
- **Par 2 (Substituição médica):** `880503` (`AWAITING_DOCTOR`, 5 dias atrás) +
  `880504` (`AWAITING_DOCTOR`, hoje), origem `Unidade Origem S5 Beta`.
- **Par 3 (Repetição de agendamento):** `880505` (aprovado →
  `SCHEDULER_REQUESTED`) + `880506` (`AWAITING_DOCTOR`), origem
  `Unidade Origem S5 Gama`.
- **Par 4 (Negativa histórica tardia):** `880507` (negado com motivo
  `Ausência de critério hemodinâmico recente` → `FINAL_REPLY_POSTED`, 3 dias
  atrás) + `880508` (`AWAITING_DOCTOR` com evento `CASE_LLM2_COMPLETED`), origem
  `Unidade Origem S5 Delta`.

## Jornadas (via entrypoint oficial)

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd drive --run <tmp/verification/hmd-verify-...> --journey s5
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

- **Mobile 390×844 (`s5-mobile-*`) em `880502` (antes de responder):**
  - Login real como `verify-nir`, abertura de `880502`: 0 fetches eager de
    embed.
  - `Visualizar PDF atual` → viewer 200 + serve 200 → `Voltar` retorna a `880502`.
  - `Visualizar PDF anterior` → viewer 200 + serve 200 → `Voltar` retorna a `880502`.
  - `#conferencia-reapresentacao` permanece intacto após abrir ambos PDFs.
- **Desktop 1280×800 (`s5-desktop-*`) como `verify-nir` (não-criador):**
  - `880502`: comparativo com `880501` e `Unidade Origem S5 Alfa`, exatamente 2
    botões (`É reapresentação do mesmo pedido` e `É um pedido independente`),
    0 fetches eager, lazy PDFs atual e anterior retornando HTTP 200 ao expandir.
  - Silêncio: navegar para `/intake/cases/` sem clicar e voltar a `880502`
    mantém `#conferencia-reapresentacao` presente.
  - Clicar `É um pedido independente` → flash informativo, `#conferencia-reapresentacao`
    desaparece e `880501` e `880502` permanecem `Aguardando decisão médica`.
  - `880504` → `É reapresentação do mesmo pedido` → flash `substituição aplicada
    na fila médica`, `880504` em `Aguardando decisão médica` e `880503` em
    `Caso concluído` (`/intake/closed-cases/?q=880503`).
  - `880506` → `É reapresentação do mesmo pedido` → flash `arquivado vinculado`
    e `880506` em `Caso concluído`.
  - `880508` → `É reapresentação do mesmo pedido` → flash `contexto histórico
    confirmado` e `880508` em `Aguardando decisão médica`.
- **Visão do Médico (`verify-doctor`):**
  - `880508`: card Prior-case exibe `Confirmado pelo NIR`, motivo real
    `Ausência de critério hemodinâmico recente` e badge `Vinculado após a sumarização`.
- **Visão do Agendador (`verify-scheduler`):**
  - Fila `/scheduler/`: `880505` exibe `Caso reapresentado 1 vez enquanto
    aguarda agendamento`.
  - Detalhe de `880505`: `#reapresentacoes-recebidas` exibe `880506`, sem
    triagem clínica/suspensão, confirmação real → `Agendamento confirmado.`.

Evidência em `<run>/artifacts/` (`report-s5.json`, `s5-*.png/txt`,
`observed-state-s5.json`). Exit `0` PASS · `1` falha observada · `2`
infra/driver.
