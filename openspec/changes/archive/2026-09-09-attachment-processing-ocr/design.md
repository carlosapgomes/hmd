# Design: attachment-processing-ocr

Referências: plano §9 (anexos), §8 (`VISION_MODEL` via OpenRouter
OpenAI-compatible; anuência do dono p/ PII externo registrada 2026-09-07 em
`temp/respostas2.md` H6/H7); padrões dos changes 04 (upload/storage/worker
pdf), 05 (anonimização/fail-closed), 06 (signals com
`payload["source"]`+`on_commit`, prompts versionados, cliente LLM), 07
(presenter re-identificado), 09 (fecho/limpeza); ats-web `CaseAttachment`
(somente-leitura — formato de row/upload path; supressão e fase supplemental
NÃO são herdadas).

## D1 — App `apps/attachments` + model `CaseAttachment`

App dedicado (FK cross-app `Case`, precedente `cases→accounts`), dono do
worker/serviços de anexo; o núcleo `apps/cases` permanece só com o ciclo do
caso. Migration `apps/attachments/migrations/0001_initial`. Row (espelho
enxuto do ats-web, sem supressão/fase):

- `case` FK `PROTECT` `related_name="attachments"`; `file` FileField
  (`case_attachments/<case_id>/<uuid4-hex>.<ext>` — UUID gerado no path
  callable, padrão `case_document_upload_path`: nome original NUNCA no path,
  arquivo gravado **antes do INSERT** com compensação best-effort do 04);
  `original_filename`, `content_type`, `size_bytes`; `uploaded_by` FK User `PROTECT`; `created_at`;
  `content_type`, `size_bytes`; `uploaded_by` FK User `PROTECT`; `created_at`;
- processamento: `status` (`pending|processing|processed|failed`, default
  `pending`), `extraction_method` (`local_pdf|vision`), `extracted_text`
  (Text), `anonymized_text` (Text), `pseudonym_map` (JSON, namespace do
  anexo — ver D4), `patient_match` (`match|mismatch|unknown`, null),
  `verification_summary` (Text), `verification_evidence` (Text),
  `processed_at`, `failed_reason`.

Eventos canônicos (aditivos em `apps/cases/events.py`):
`CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED` (auditoria de PII externo:
payload filename/método), `CASE_ATTACHMENT_PROCESSED` (payload
match/método), `CASE_ATTACHMENT_FAILED` (payload motivo). Sem evento de
upload (a row é o registro).

## D2 — Upload no intake (kwargs aditivos) + listagem NIR

`create_case_with_documents` ganha kwarg opcional `attachments:
Sequence[UploadedFile] = ()` (aditivo; default preserva o change 04 —
regressão testada) e `create_corrected_resubmission` repassa. Validação
própria em `apps/attachments/services.py::validate_attachments` (fonte
única): contagem ≤ `ATTACHMENTS_MAX_COUNT` (5), tamanho ≤
`ATTACHMENTS_MAX_SIZE_MB` (10 MB) por arquivo, MIME ∈ {image/jpeg,
image/png, application/pdf} — erros nomeados ANTES de qualquer gravação
(padrão `_validate_batch`). Files gravados no MESMO atomic da criação do
caso (compensação best-effort como o change 04). Detalhe do NIR ganha bloco
"Anexos": nome/tamanho/tipo + status legível (sem conteúdo de verificação —
o resultado clínico é do médico).

## D3 — Extração híbrida + worker + trigger

**`apps/attachments/extraction.py`**: por anexo — PDF com camada de texto
(soma de texto das páginas > limiar mínimo) → PyMuPDF local
(`extraction_method=local_pdf`); senão (imagem OU PDF-imagem; rasterização
de páginas via pixmap, teto de 10 páginas/anexo) → `apps/attachments/
vision.py::transcribe_image(image_bytes, content_type) -> str` via
`settings.VISION_MODEL` na OpenRouter (SDK OpenAI, image data-URL;
`extraction_method=vision`; **evento de auditoria antes do envio**).
`vision.py` é cliente próprio fino (mesma env/base/timeout do
`apps/pipeline/llm.py`; reutiliza `LlmError`; NÃO estende o protocolo
`LlmClient` — domínio diferente, zero toque no contrato do change 06).

**Worker**: task `process_case_attachments(case_id)` (idempotente **por
etapa**: anexo com `extracted_text` já presente não re-extraí nem re-envia
ao OCR externo; re-leitura da row antes de cada gravação/evento — anexo
removido pela ciência do NIR → no-op silencioso sem evento, corrida
ack×worker; retry do q2 nunca duplica o envio externo) no **cluster
`attachments`** (novo em
`Q_CLUSTER["ALT_CLUSTERS"]`; flag `ATTACHMENTS_RUN_TASKS_INLINE`, default
true em dev/teste — padrão dos clusters pdf/anonymization/llm). Pipeline por
anexo: extração → anonimização (D4) → verificação (D4) → persistência +
evento; **falha em qualquer etapa → `failed` com `failed_reason` + evento
`CASE_ATTACHMENT_FAILED` (fail-closed por anexo, caso nunca bloqueado —
anexo é suplementar)**.

**Trigger**: signal em `apps/attachments/signals.py` no padrão do
`apps/pipeline/signals.py` — `CaseEvent.post_save` filtra
`CASE_ANONYMIZATION_COMPLETED` (mapa do caso pronto) → `transaction.on_commit`
→ enqueue. Anexos processam **em paralelo ao pipeline LLM** ("após o
relatório principal" = relatório extraído+anonimizado; leitura registrada
aqui). Caso sem anexos: task sai imediato. Caso já em `AWAITING_DOCTOR`+:
cards chegam quando chegarem (status visível; decisão nunca gated).

## D4 — Anonimização alinhada + verificação (LLM só vê tokens)

**`apps/anonymization/services.py`** (aditivo):
`anonymize_text(text, seed_map=None)` — o `PseudonymOperator` ganha
**semeadura explícita** com o mapa do caso (construtor recebe os tokens do
caso pré-carregados por chave canônica `(categoria, valor_canônico)` — a
chave já existe — e a numeração de cada categoria continua do máximo do
caso; **a numeração é por primeira ocorrência no texto, portanto NÃO há
convergência espontânea entre textos — sem semeadura, `<PESSOA_1>` do anexo
não é o `<PESSOA_1>` do caso e a verificação colapsa em falsos
match/mismatch**). `anonymize_attachment_text(case, text)` chama o núcleo
com `seed_map=case.pseudonym_map` e persiste na row do anexo apenas as
entradas **efetivamente usadas no texto** (semeadas usadas + novas);
`anonymize_case_text` e o mapa do caso ficam intocados. Garantias do
namespace estendido: (i) valor igual ao de uma entidade do caso → MESMO
token do caso (paciente do caso → `<PESSOA_N>` do caso, qualquer que seja a
ordem no texto do anexo); (ii) paciente DIFERENTE no anexo → token NOVO
(número acima do máximo do caso) — **distinto** do token do caso, tornando
o mismatch detectável pelo LLM; (iii) sem colisões de token entre os dois
mapas. Helper `patient_token(case)` (reverse lookup no mapa do caso:
token cujo valor real == `case.patient_name`); sem paciente/token →
verificação segue sem comparação e o resultado é `unknown`.

**`apps/attachments/verification.py`**: prompt versionado
`ATTACHMENT_VERIFICATION` (seed em `apps/llm/prompts_seed.py` — 29º,
idempotente) + `json_schema` strict: `{patient_match:
match|mismatch|unknown, summary: str, evidence: str}`; chamada via
`get_llm_client().complete` com `LLM1_MODEL` (verificação é tarefa de texto);
entrada = texto anonimizado do anexo + token do paciente do caso.
**Invariantes**: assert recursivo de tokens vs mapa do anexo+caso no input
(padrão do change 06); resultado persistido na row + evento
`CASE_ATTACHMENT_PROCESSED`; falha LLM → `failed` (D3). `mismatch`/`unknown`
NUNCA descartam nada — são informação para o médico.

## D5 — Cards na decisão médica

`build_case_detail_context` (doctor) ganha seção **Anexos**: por anexo —
nome, método (Local/OCR externo), status, badge
`match|mismatch|unknown|processando|falhou`, `verification_summary`/
`verification_evidence` **re-identificados na renderização** com o mapa do
ANEXO via o núcleo puro `reidentify(text, pseudonym_map)` — já aceita mapa
arbitrário, **sem helper novo**; o mapa do anexo é auto-suficiente
(namespace estendido do caso, ver D4; fallback ao mapa do caso apenas
defensivo); padrão do change 07. Mismatch → badge de alerta + texto
"divergência de identificação — avalie" (sem ação automática).
Sem anexos → seção ausente (presenter intocado para casos sem anexo).

## D6 — Fechamento (integração com o change 09)

`acknowledge_case_receipt` passa a coletar também os arquivos dos anexos
(antes do delete), deletar rows `case.attachments` no atomic e removê-los
fisicamente no mesmo `on_commit` best-effort dos documentos (D2 do 09).
Preservado: nada de anexo sobrevive à ciência (não há auditoria de conteúdo
de anexo — a trilha fica nos eventos, sem conteúdo). Delta MODIFIED da spec
`case-closure` carrega TODOS os cenários existentes + 1 novo (anexos
removidos). Reenvio corrigido herda anexos como parâmetro novo (D2), sem
herança dos anexos do original (novo intake = nova seleção).

## D7 — Limites e env

`ATTACHMENTS_MAX_COUNT=5`, `ATTACHMENTS_MAX_SIZE_MB=10`,
`ATTACHMENTS_ACCEPTED_MIME_TYPES` (jpeg/png/pdf), `VISION_MODEL` (env; sem
default — fail-closed se vazio E necessário), `ATTACHMENTS_RUN_TASKS_INLINE`
(default true), `ATTACHMENTS_VISION_MAX_PAGES=10`. Docker: worker
`worker-attachments` documentado como imagem do worker-llm com cluster
diferente (sem novo Dockerfile; produção igual aos demais workers).
