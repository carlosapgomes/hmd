# Design — intake-batch-semantics

## D1 — `submit_report_batch`: molde ats-web, ordem de validação

Assinatura: `submit_report_batch(*, user, role, files, procedure_type,
attachments=()) -> tuple[list[Case], list[str]]`. Ordem (igual ao ats-web):

1. Gate `INTAKE_ENABLED` (fail-closed existente, no topo);
2. Tipo único do lote: fora do catálogo → `([], [erro])` (nada criado);
3. Limites de lote: vazio / `INTAKE_MAX_FILES_PER_BATCH` /
   `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` → `([], [erro])`;
4. Anexos (se houver): `validate_attachments(attachments,
   pdf_count=len(files))` — `pdf_count != 1` registra o erro de anexos mas
   **não bloqueia** (os casos do lote serão criados SEM anexos, erro
   reportado no fim); com 1 PDF, anexo inválido **aborta tudo** (nada é
   criado — transação do caso único);
5. Loop por arquivo: `validate_single_document` (PDF + tamanho por arquivo)
   → inválido: erro por arquivo (com o nome), continua; válido: cria o caso;
6. Fim: se exatamente 1 caso criado + anexos válidos → anexos gravados na
   transação do caso (`upload_phase="initial"`); erros de anexo pendentes
   são anexados à lista.

Cada caso nasce em `NEW` com **1 documento**, o **tipo do lote**, evento de
declaração e o enfileiramento existente da extração — nada do cluster/FSM
muda (são N casos independentes entrando no fluxo que já existe).

## D2 — `create_case_with_documents` vira primitiva de caso único

Refatoração interna: cria **um** caso a partir de **um** arquivo, `procedure_type`
único e anexos opcionais, na transação atômica existente (usada pelo lote e
pelo reenvio). Parâmetro `files`/`procedure_types` (listas) são substituídos
por `file`/`procedure_type` — callers controlados (batch, resubmission,
views, testes). Nada de N-documentos por caso em nenhuma entrada do sistema.

## D3 — Settings de lote (e o limite real do Cloudflare)

- `INTAKE_MAX_FILES_PER_BATCH` (default **30**, molde ats-web);
- `INTAKE_MAX_UPLOAD_BYTES_PER_FILE` (default **20 MB** — igual ao
  `INTAKE_MAX_FILE_MB` atual, que passa a derivar/migra);
- `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` (default **100 MB**): ats-web usa
  600 MB, mas o piloto entra por túnel Cloudflare que limita o corpo da
  requisição (~100 MB no plano free) — 600 MB morreria na borda com erro
  opaco. 100 MB default, env-tunable; documentar no README/.env.example.
- `INTAKE_MAX_DOCUMENTS` (10, "documentos por caso") fica **óbsoleto** e é
  removido (substituído pelo limite de lote); grep de usos antes de remover.

## D4 — Form/UI: tipo único, hints numéricos, anexos desabilitáveis

- `procedure_type = ChoiceField` (choices do catálogo, widget radio) —
  substitui o `MultipleChoiceField` de checkboxes; help "um tipo por envio,
  aplicado a todos os relatórios do lote".
- `documents`: help com os números dinâmicos dos settings ("cada PDF é um
  relatório de um paciente e vira um caso; até N arquivos, X MB cada, Y MB
  no total").
- `attachments`: help acrescenta "somente quando o envio tiver exatamente 1
  relatório".
- JS vanilla (`static/js/intake-upload.js`, registrado no template): no
  `change` do input de documentos, `input.files.length > 1` → desabilita o
  input de anexos (`disabled` + classe visual) e exibe hint; 1 arquivo →
  reabilita. Server-side continua sendo a fonte da verdade (D1.4).
- Template de resultado: seção com "N casos criados" + lista de erros por
  arquivo (nome + motivo) — molde do resultado `(cases, errors)`.

## D5 — Reenvio corrigido: exatamente 1 PDF + anexos + tipo único

`create_corrected_resubmission` (molde ats-web): valida **exatamente 1 PDF**
(erro nomeado caso contrário), tipo único redeclarável (pode corrigir o tipo
do caso), anexos permitidos (`upload_phase="corrected"`), transação atômica
do caso corrigido. `CorrectedResubmissionForm` herda o form novo (tipo único
já vem por herança; o campo de documentos já valida 1 arquivo no serviço).

## D6 — Especificação e testes

- `intake-nir` REMOVED ("Criação de caso com upload multi-PDF e declaração
  de tipos") + ADDED ("Envio em lote: um PDF por caso, tipo único") — os
  cenários antigos ("2 PDFs → 1 caso com 2 documentos e 2 tipos") descrevem
  comportamento que deixa de existir.
- `attachments` MODIFIED ("Upload e listagem de anexos pelo NIR"): texto +
  cenário 1 reescritos (anexos com exatamente 1 PDF; no lote multi-PDF os
  casos são criados sem anexos e o erro informa a restrição).
- Testes: serviço (lote cria N casos; parcial; anexos×pdf_count; limites;
  tipo único inválido), form/UI (radio único, hints, JS/attrs), view
  (resultado do lote), reenvio (1 PDF obrigatório + anexos).
