# Revisão NIR do procedimento (slice 002)

Prova da confirmação explícita do NIR nos novos envios
(`confirm-nir-procedure-review`, slice 002, R1–R5): marca prospectiva na
primitiva única, bypass simples recusado no marcado (legado preservado),
card declarado×detectado com select único dos 13 tipos (atual
pré-selecionado e confirmável, inclusive não detectado), justificativa
1–500 + leitura explícita sem upload, rota/form/view sobre os serviços
reais de 001, negativos endurecidos sem mutação e o adaptador LLM
determinístico exclusivo de verify com fixtures `990101–990104`.
Sobre a instância isolada do `verify-hmd`, com `seed_verify
--procedure-review` sob a identidade/banco/media da run.

## Pré-condição (setup real, nunca prova)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
# sob a identidade da run (db/media do manifest):
VERIFY_DB_NAME=<manifest.database> VERIFY_DB_PORT=<manifest.dbPort> \
  HMD_VERIFY_MEDIA_ROOT=<run>/media \
  uv run python manage.py seed_verify --procedure-review --settings=config.settings.verify
```

O setup chama `seed_prompts` antes dos casos e cria, pela primitiva e
pelo pipeline reais com o adaptador D9, a retenção por divergência:
`990101` (manter `art_perif`), `990102` (trocar
`flebografia`→`angio_art_perif`), `990103` (detecção vazia,
`nefrostomia`), `990104` (trocar `dren_biliar`→`art_cerebral` com falha
tipada da LLM2 pós-confirmação). Repetir o setup antes/depois de
confirmar não duplica nem reseta nada (mesmos ids, eventos, estado,
versões de prompts); conflito de fixture é erro explícito.

## Jornadas

- `review-card-gating` (R1–R3): `verify-nir` logado pelo form →
  detalhe de `990101` mostra o card *Revisão do procedimento* com o
  resumo declarados×detectados e o form (select com 13 opções e o
  atual selecionado, justificativa, leitura, hiddens de revisão/fonte,
  sem input de arquivo e sem botão *Liberar caso*); o caso legado
  `VERIFY-001` (sem marca, sem retenção) não mostra o card.
- `review-maintain` (R3/R5, com JS): em `990101`, preenche a
  justificativa, marca a leitura e confirma mantendo `art_perif` →
  redirect ao detalhe com flash na referência humana (`Caso 990101…`,
  sem uid); o card some (caso avançou); login como `verify-doctor` →
  `990101` aparece em *Casos aguardando decisão* (análise produzida
  pelo servidor: nova LLM1 com detecção repetida + LLM2).
- `review-switch-to-undetected` (R3/R5, com JS): em `990102`, troca o
  select para `Angioplastia arterial periférica (membros)` via teclado,
  justifica, marca a leitura e confirma → detalhe sem retenção;
  `verify-doctor` vê `990102` na fila (análise do singleton confirmado).
- `review-fields-keyboard` (R3/R4, com JS): Tab alcança select →
  justificativa → checkbox → submit; erro de justificativa vazia exibe
  alerta sem marcar a leitura automaticamente; tentativa com XSS é
  reexibida escapada.
- `review-nojs` (R3/R4, sem JS): contexto com `javaScriptEnabled:
  false` → select nativo com 13 opções funciona e confirma `990103`
  (mantendo `nefrostomia`, detecção vazia) só pelo POST do form.
- `review-viewports` (R5): `990101`/`990102` em 390 e 1280 sem overflow
  horizontal (`scrollWidth <= clientWidth`), com screenshot+t snapshot.
- `review-zero-external-transport` (R5/D9): durante todas as jornadas,
  nenhuma resposta bem-sucedida vem de host não-loopback e nenhuma
  requisição mira hosts de LLM (`openrouter*`, `api.*`); tentativas a
  CDNs estáticos pré-existentes (fonts/bootstrap/htmx) são registradas
  à parte e a jornada passa com elas bloqueadas (prova de
  independência de rede externa).

## Como dirigir (Playwright)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
VERIFY_DB_NAME=<manifest.database> ... seed_verify --procedure-review ...
node .pi/skills/verify-hmd/scripts/browser/drive-procedure-review.mjs --run <tmp/verification/hmd-verify-...>
.pi/skills/verify-hmd/scripts/control-hmd cleanup --run <tmp/verification/hmd-verify-...>
```

Evidência em `<run>/artifacts/` (`report.json`,
`procedure-review-*.png/txt`, `observed-state.json`). Exit `0` PASS ·
`1` falha observada · `2` infra/driver (Chromium pinado indisponível =
**BLOCKED**, nunca trocar o motor).
