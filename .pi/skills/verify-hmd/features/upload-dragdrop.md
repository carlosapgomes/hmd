# Upload com drag-and-drop e preview (slice 001)

Prova do upload NIR no padrão ats-web, sobre os fields/limites/semântica
HMD (ui-parity-intake-detail, slice 001, R1–R4): zona drag-and-drop +
preview (lista, contagem, tamanho total), toggle de anexos 1→2→1,
submissão sem gate de confirmação, lote misto com erro server-side
nomeado e form nativo sem JS. Sobre a instância isolada do `verify-hmd`.

## Jornadas

- `upload-preview-dragdrop`: `verify-nir` logado pelo form → `/intake/`
  com a zona visível e o preview oculto → seleção nativa de 2 PDFs →
  preview com `2 arquivo(s)`, 2 itens na lista, tamanho total e anexos
  desabilitados com o hint *Anexos só com exatamente 1 relatório* →
  *Remover* de um item → `1 arquivo(s)` e anexos reabilitados →
  drop com `DataTransfer` (`arrastado.pdf`) → o arrastado entra no
  preview, é **mantido no preview até o submit** e sincronizado ao
  input (`dragged-synced-to-input`) — o caso criado no detalhe lista
  `arrastado.pdf` (os arrastados são os submetidos).
- `upload-attachment-no-confirmation` (R4): com o arrastado + 1 anexo
  (`foto.jpg`) e o tipo `art_perif`, o submit vai direto ao detalhe
  do caso — **nenhum** checkbox de confirmação existe na página (o
  gate do ats-web não foi portado).
- `upload-mixed-batch` (R1): 1 PDF válido + 1 arquivo com extensão
  `.pdf` mas content-type não-PDF + 1 não-PDF pela extensão
  (`foto.jpg`) → o válido cria o caso, a página de resultado nomeia
  **ambos** os inválidos e o contexto do envio é preservado. (O
  cliente nunca exclui: não-PDF, duplicados e acima-dos-limites são
  submetidos e o servidor, autoridade, os nomeia — fix-1 P1-A.)
- `upload-invalid-attachment` (P1-A, specs/attachments): 1 PDF + 1
  anexo MIME não aceito (`foto.gif`) → o cliente mantém o anexo no
  preview, submete e o servidor **abort** — sem redirect ao detalhe,
  sem caso criado. Paridade no reenvio: pytest.
- `upload-duplicates` (R1): 2 PDFs com o mesmo nome → ambos no
  preview e sincronizados ao input → `2 casos criados` (o servidor
  processa arquivo a arquivo).
- `upload-overlimit` (R1/P1-A): 31 PDFs (acima dos 30) → os 31 no
  preview e sincronizados ao input → o servidor rejeita o **lote
  inteiro** (`0 casos criados`, *Máximo de 30*).
- `upload-nojs` (R2): contexto com `javaScriptEnabled: false`, login
  pelo form, **2 PDFs** + tipo pelo form nativo → `2 casos criados`
  sem nenhum JS (mesmo lote multi-arquivo).
- `resubmit-gated` (R3, gate no browser): a URL de reenvio de um
  caso não-`CLEANED` redireciona ao detalhe (ainda provado, junto
  com a jornada real abaixo).
- `upload-multi-attachment` (P1-1, fix-2, R1): 1 relatório + anexo
  (`foto.jpg`), arrastar o 2º relatório (`segundo.pdf`) → a edição
  desabilita os anexos e mostra o hint, mas o preview e os arquivos
  do input são preservados — no submit o input é reabilitado e o
  anexo já selecionado é submetido com >1 relatório: o servidor
  cria os 2 casos sem anexos e emite o erro não-bloqueante nomeado
  (*exatamente 1*). Antes do fix, o navegador omitia o anexo
  `disabled` e o erro nunca aparecia.
- `resubmit-real` (P1-2, fix-2, R3/R4): o `VERIFY-001` sintético
  nasce `AWAITING_DOCTOR` no seed — o médico nega todos os
  procedimentos (resposta final publicada), o NIR criador confirma
  o recebimento até `CLEANED` (*Caso concluído*, *Reenviar corrigido*
  visível) e reenvia 1 PDF (`corrigido.pdf`) + anexo no widget do
  reenvio (submit sem confirmação, sem checkbox) até o detalhe do
  NOVO caso, que lista `corrigido.pdf` e `foto.jpg`. Tudo pela UI
  real, sem mutação direta de banco nem mudança de seed/backend.
  O anexo inválido do reenvio segue por paridade pytest
  (`test_corrected_resubmission_with_invalid_attachment_aborts`).

## Como dirigir (Playwright)

```bash
.pi/skills/verify-hmd/scripts/control-hmd doctor --run <tmp/verification/hmd-verify-...>
node .pi/skills/verify-hmd/scripts/browser/drive-upload-dragdrop.mjs --run <tmp/verification/hmd-verify-...>
```

O drive recusa rodar sem posse da instância (doctor prévio via
`control-hmd doctor`). Capturas: `nir-upload-before`,
`nir-upload-preview-2`, `nir-upload-dragdrop`,
`nir-upload-submitted-detail`, `nir-resubmit-gated`,
`nir-upload-mixed-result`, `nir-upload-invalid-attachment`,
`nir-upload-duplicates-result`, `nir-upload-overlimit`,
`nir-upload-nojs-before/after`, `nir-upload-multi-attachment-before/result`,
`doctor-resubmit-decide/decided`, `nir-resubmit-before-ack/cleaned/form/preview`,
`nir-resubmit-submitted-detail` (+ `report.json`).

## Notas

- O tipo `art_perif` é selecionado só por seletores (migração P2 do
  slice 002, sem mudança de comportamento de upload): jornadas com
  JS digitam o código no combobox + `Enter`, a jornada sem JS usa o
  select nativo (`selectOption`).
- Os PDFs da jornada são mínimos válidos gerados no drive; o
  processamento inline pode falhar depois da criação (sem LLM no
  verify) — a prova é a criação/redirect, não o pipeline.
- Sem JS o input segue visível dentro da zona (nunca `d-none`): é o
  que garante o R2; com JS, o clique na zona (fora do input) abre o
  mesmo seletor nativo.
