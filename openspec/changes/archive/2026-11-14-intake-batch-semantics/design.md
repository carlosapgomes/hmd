# Design — intake-batch-semantics (rev. 2, pós-review do plano)

Reviewer: BLOCK → rework (2 P0, 3 P1, P2s incorporados).

## D1 — `submit_report_batch`: molde ats-web, ordem de validação

Assinatura: `submit_report_batch(*, user, role, files, procedure_type,
attachments=()) -> tuple[list[Case], list[str]]`. Ordem (igual ao ats-web):

1. Gate `INTAKE_ENABLED` (fail-closed existente, no topo — entra no
   inventário do `test_intake_lock.py`);
2. Tipo único do lote: ausente/fora do catálogo → `([], [erro])`;
3. Limites de lote: vazio / `INTAKE_MAX_FILES_PER_BATCH` /
   `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` → `([], [erro])`;
4. Anexos (se houver): `validate_attachments(attachments,
   pdf_count=len(files))` — `pdf_count != 1` registra erro NÃO-bloqueante
   (casos serão criados sem anexos); com 1 PDF, anexo inválido ABORTA tudo;
5. Loop por arquivo: `validate_single_document` (PDF + tamanho por arquivo)
   → inválido: erro com o nome do arquivo, continua; válido: cria o caso
   (transação própria);
6. Fim: se exatamente 1 caso criado + anexos válidos → anexos gravados na
   transação do caso (`upload_phase="initial"`).

**Falha de persistência no meio do lote (contrato explícito, review P2)**:
cada caso é uma transação independente; exceção inesperada na criação do
caso k (storage/DB) é capturada no loop e virará erro por arquivo
(nome + "erro interno"), preservando os casos já criados — mesmo contrato
de parcialidade das validações, sem 500 silencioso.

**Atomicidade por caso (review P2)**: cada caso nasce atômico (caso `NEW` +
1 documento + declaração do tipo + eventos + anexos quando couberem, juntos
ou nada) — cláusula explícita no requisito ADDED.

## D2 — Primitiva de caso único vale para TODAS as entradas (P0-1)

"Nada de N-documentos por caso em **nenhuma** entrada" agora inclui as TRÊS
entradas existentes: (a) envio inicial (`submit_report_batch`), (b) reenvio
corrigido (`create_corrected_resubmission`, D5) e (c) **reenvio de
documentos do gate** (`resubmit_case_documents`, esquecido na rev. 1) —
este passa a exigir **exatamente 1 PDF** (erro nomeado caso contrário,
nada alterado), substituindo os documentos do caso retido e reprocessando
como hoje; seu limite de contagem migra de `INTAKE_MAX_DOCUMENTS`
(obsoleto) para a validação de arquivo único (tamanho por arquivo). UI do
gate (`case_detail` do NIR) perde o `multiple`/hint de ordem. Spec
"Revisão NIR do gate" MODIFIED neste change.

## D3 — Settings literais + docs (P2)

- `INTAKE_MAX_FILES_PER_BATCH = int(os.environ.get(..., "30"))`;
- `INTAKE_MAX_UPLOAD_BYTES_PER_FILE = int(os.environ.get(..., str(20*1024*1024)))`
  — literal em bytes (molde ats-web); helper MB local para mensagens;
  `INTAKE_MAX_FILE_MB` é REMOVIDO (usos em services migrados);
- `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` default **100 MB** — recomendação
  operacional do piloto (túnel Cloudflare limita request body ~100 MB no
  plano free; NÃO é constante externa verificável no repo): env-tunable,
  documentada em README/.env.example/compose com `${VAR:-default}` (nunca
  string vazia — pitfall já documentado do compose);
- `INTAKE_MAX_DOCUMENTS` removido com grep de usos (services/forms/env).
- README ganha a seção dos limites (slice 002 — promessa da rev. 1 agora
  com dono).

## D4 — Form/UI

Como rev. 1 (radio único, hints numéricos dinâmicos, JS
`static/js/intake-upload.js` desabilitando anexos com >1 arquivo, resultado
do lote) + (P1-3/P2): `templates/accounts/manual.html` atualizado (frase
dos "1 a N PDFs / ao menos um tipo" → semântica nova); strings do h1/links
da home preservadas (`test_home_dispatch.py` depende delas).

**Contrato redirect×resultado (P2)**: POST com exatamente 1 caso criado e
ZERO erros → **redirect ao detalhe do caso** (comportamento atual,
`test_my_cases.py:285-301` preservado); lote (N>1) e/ou erros → página de
resultado ("N casos criados" + erros por arquivo + link Meus casos).

## D5 — Reenvio corrigido (movido p/ o slice 001 — P0-2)

A mudança de assinatura da primitiva atingiria `create_corrected_resubmission`
no primeiro slice de qualquer forma (kwargs de lista) — a semântica nova do
reenvio (**exatamente 1 PDF** + tipo único redeclarável + anexos
`upload_phase="corrected"`, molde ats-web) entra no SLICE 001 junto com as
reescritas de `test_corrected_resubmission.py`; o slice 003 encolhe para
UI/hints do reenvio. Spec `case-closure` MODIFIED (reenvio: 1 PDF, tipo
único).

## D6 — Especificação e testes

- `intake-nir` REMOVED+ADDED (rev. 1) + cláusula de atomicidade por caso +
  cenário "casos nascem em NEW" com nota `INTAKE_RUN_TASKS_INLINE=False`
  nos testes (inline pinado no test.py) + MODIFIED "Revisão NIR do gate"
  (reenvio de documentos = exatamente 1 PDF).
- `attachments` MODIFIED com cenários pinados em "exatamente 1 PDF".
- `case-closure` MODIFIED "Reenvio corrigido cria novo caso vinculado".
- Gate de regulação: hoje roda UMA vez por caso sobre o texto concatenado
  dos documentos — com 1 doc/caso vira genuinamente por-relatório (relatório
  malformado não é mais mascarado pela concatenação); sem mudança de código,
  nota no slice 001.
- Inventário fail-closed: `submit_report_batch` entra no
  `test_intake_lock.py`; POSTs do reenvio do gate continuam cobertos.
