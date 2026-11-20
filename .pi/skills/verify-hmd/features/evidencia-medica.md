# Evidência médica da confirmação (slice 004)

Prova de que o médico vê a contradição e decide sobre a análise confirmada
(`confirm-nir-procedure-review`, slice 004, R1–R5): aviso determinístico
`APPLIED` + `CONFIRMED` referenciada no detalhe e na decisão, autor/data/
justificativa escapada e ressalva clínica; reroteamento por subtipo,
singleton na decisão sem veto clínico, defesa de completude no trabalho e
ausência de aviso fictício. Reutiliza o adaptador/fixtures D9 do slice 002
(`seed_verify --procedure-review`, nºs `990101–990104`).
Sobre a instância isolada do `verify-hmd`, com chave externa vazia.

## Pré-condição (setup real, nunca prova)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
# sob a identidade da run (db/media do manifest):
VERIFY_DB_NAME=<manifest.database> VERIFY_DB_PORT=<manifest.dbPort> \
  HMD_VERIFY_MEDIA_ROOT=<run>/media \
  uv run python manage.py seed_verify --procedure-review --settings=config.settings.verify
```

`seed_verify --procedure-review` chama `seed_prompts` e cria, pela primitiva
e pipeline reais com o adaptador D9, a retenção por divergência: `990101`
(manter `art_perif`), `990102` (trocar `flebografia`→`angio_art_perif`),
`990103` (detecção vazia, `nefrostomia`), `990104` (trocar
`dren_biliar`→`art_cerebral` com falha tipada da LLM2 pós-confirmação).
CONFIRMAÇÃO/APPLIED/resumo/análise nascem SÓ do POST real do navegador —
nunca do seed. Repetir o setup não duplica nem reseta jornadas já
confirmadas/decididas (mesmos ids, eventos, estado, prompts).

## Jornadas

- `evidence-maintain-notice` (R1–R2/R5): `verify-nir` logado pelo form → se
  `990101` ainda exibe o card *Revisão do procedimento*, confirma mantendo
  `art_perif` (justificativa com `<b>` para a prova de escaping); `verify-doctor`
  → fila contém `990101` → detalhe mostra *Procedimento confirmado pelo NIR*
  com seleção mantida, detecção original, autor/data, justificativa escapada
  (`&lt;b&gt;`, sem `<b>leitura</b>` cru) e a ressalva clínica — mais o sumário
  clínico da análise confirmada.
- `evidence-switch-reroute` (R1–R3/R5): `990102` confirmado para
  `angio_art_perif` (se ainda não confirmado) → detalhe do médico distingue
  anterior (`Flebografia`), confirmado e detecção nova — sem união; a matriz
  por subtipo segue a declaração efetiva (caso acessível na fila).
- `evidence-empty-detection` (R1–R2/R5): `990103` confirmado mantendo
  `nefrostomia` → aviso informa *Nenhum procedimento detectado na nova
  extração* sem afirmar concordância do LLM.
- `evidence-decision-not-vetoed` (R3): em `990101`, o médico abre a decisão
  (aviso visível junto ao formulário) e aprova — se ainda não decidido — com
  reserva válida; redirect com *Decisão registrada* (consultivo nunca veta).
  Reexecução com caso já decidido: detalhe read-only com *Decisões
  registradas*, sem segunda decisão.
- `evidence-error-never-ready` (R4): `990104` confirmado para `art_cerebral`
  (se ainda não confirmado) → `FAILED` → a fila médica NÃO o lista como
  pronto; o PDF original segue consultável no detalhe autorizado.
- `evidence-viewports` (R5): detalhe com aviso em 390 e 1280 sem overflow
  horizontal (`scrollWidth <= clientWidth`), com screenshot+snapshot.
- `evidence-zero-external-transport` (R5/D9): durante todas as jornadas,
  nenhuma resposta bem-sucedida vem de host não-loopback e nenhuma requisição
  mira hosts de LLM (`openrouter*`, `api.*`); a jornada passa com elas
  bloqueadas (prova de independência de rede externa).

## Como dirigir (Playwright)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
VERIFY_DB_NAME=<manifest.database> ... seed_verify --procedure-review ...
node .pi/skills/verify-hmd/scripts/browser/drive-doctor-evidence.mjs --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

Evidência em `<run>/artifacts/` (`report.json`,
`doctor-evidence-*.png/txt`, `observed-state.json`). Exit `0` PASS ·
`1` falha observada · `2` infra/driver (Chromium pinado indisponível =
**BLOCKED**, nunca trocar o motor).
