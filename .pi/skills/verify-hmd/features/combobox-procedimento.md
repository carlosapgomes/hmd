# Combobox de tipo de procedimento (slice 002)

Prova do `procedure_type` pesquisável no padrão ats-web, com select
canônico funcionando sem JS e a mesma validação (ui-parity-intake-detail,
slice 002, R1–R3): busca por nome/código ignorando acentos, teclado
↑↓/Enter, texto visível e valor submetido nunca divergem (editar/limpar
pós-seleção limpa o canônico), vazio/sem-match/não-confirmado não
submetem valor, erro associado e acessível. Sobre a instância isolada do
`verify-hmd`. Busca restrita a labels + códigos dos 13 perfis — sem
sinônimos (deferidos com nota de produto; o hint não promete sinônimos).

## Jornadas

- `combobox-keyboard-select` (R1, com JS): `verify-nir` logado pelo
  form → `/intake/` com o combobox (input `role="combobox"`, select
  canônico recolhido) → digita `cardiaco` (sem acento) → a listbox
  filtra só *Cateterismo cardíaco* → `ArrowDown`/`Enter` → o input
  mostra o label e o select vale `cat_cardiaco` (`--selected`).
- `combobox-filter-by-code` (R3, com JS): digita o código
  `nefrostomia` → só *Nefrostomia percutânea* na listbox; clique na
  opção → select vale `nefrostomia`. Nenhuma opção fora do catálogo
  aparece em nenhuma busca.
- `combobox-edit-clears-canonical` (R1/R2, com JS): após confirmar
  `cat_cardiaco`, edita o texto (acrescenta 1 caractere) → o select
  volta a `""`; limpa o input → segue `""`. Texto visível e submetido
  nunca divergem.
- `combobox-no-match` (R2, com JS): digita texto sem match → mensagem
  *Nenhum procedimento encontrado* e select segue `""`.
- `combobox-unconfirmed-submit` (R2, com JS): digita `cateterismo`
  (parcial, sem confirmar) + PDF válido → submit → erro *Este campo é
  obrigatório.* com `aria-invalid`/`aria-describedby` e `role="alert"`,
  sem criar caso.
- `combobox-valid-submit` (R2, com JS): confirma `cat_cardiaco` pelo
  teclado + PDF → submit vai ao detalhe do caso criado.
- `combobox-nojs` (R2, sem JS): contexto com `javaScriptEnabled:
  false`, login pelo form, select nativo visível → `selectOption`
  `cat_cardiaco` + PDF → caso criado sem nenhum JS; submit vazio →
  *Este campo é obrigatório.*
- `combobox-label-accessible-name` (P1-A, com e sem JS): o combobox
  expõe nome acessível *Tipo de procedimento* (`role="combobox"` com
  nome; sem JS o select nativo tem o mesmo nome) via label com id
  estável (`id_procedure_type_label`, `aria-labelledby` no input com
  JS e `for` no select sem JS); o clique no label foca o input do
  combobox (e abre a listbox) nos dois fluxos (home e reenvio).
- `combobox-end-scroll-visible` (P1-B, com JS): abre a listbox,
  `End` arma a última opção (`nefrostomia`) já rolada para dentro
  da viewport (8ª+ opção além da altura inicial de 16rem) e `Enter`
  confirma esse canônico — nenhuma opção ativa fica off-screen.
- `resubmit-combobox` (R2, com JS): o `VERIFY-001` sintético é
  decidido (negado), confirmado pelo NIR até `CLEANED` e reenviado no
  combobox do reenvio (busca por código `nefrostomia` + Enter) até o
  detalhe do NOVO caso. Tudo pela UI real, sem mutação direta de banco
  nem mudança de seed/backend.

## Como dirigir (Playwright)

```bash
.pi/skills/verify-hmd/scripts/control-hmd launch
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-combobox-procedimento.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor`). Capturas: `nir-combobox-before`,
`nir-combobox-filtered`, `nir-combobox-selected`,
`nir-combobox-edit-cleared`, `nir-combobox-nomatch`,
`nir-combobox-unconfirmed-error`, `nir-combobox-submitted-detail`,
`nir-combobox-nojs-before/after`, `doctor-resubmit-decide/decided`,
`nir-resubmit-before-ack/cleaned/form`, `nir-resubmit-combobox`,
`nir-resubmit-submitted-detail` (+ `report.json`).

## Notas

- Os PDFs da jornada são mínimos válidos gerados no drive; o
  processamento inline pode falhar depois da criação (sem LLM no
  verify) — a prova é a criação/redirect, não o pipeline.
- Com JS o select fica recolhido no DOM (valor submetido) e o input
  expõe o mesmo estado (`aria-expanded`, `aria-activedescendant`);
  sem JS o select segue visível — é o que garante o R2 nos dois modos.
- Sinônimos não são buscáveis por decisão de produto (R3): termo que
  não é substring de nenhum label nem código (ex.: `stent`) não
  filtra nada — só labels e códigos canônicos.
