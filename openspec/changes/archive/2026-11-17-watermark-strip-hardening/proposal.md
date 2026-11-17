# Proposal: watermark-strip-hardening

## Why

A remoção de marca d'água do HMD (`strip_watermark`) tem dois gaps expostos pela
comparação com o ats-web e o sarv3: (1) só nomeia marca que renderiza como
**banda intra-linha cheia** (mesmo token 4+× na mesma linha) — marca que
renderiza como **linha solta** ou com **poucas repetições por linha** (ex.:
"48291 48291", forma diagonal do sarv3; linhas soltas do corpus ats-web)
atravessa sem ser removida, em qualquer tamanho de dígito; (2) o sweep
residual varre o token nomeado **globalmente** — valores legítimos de 5–6
dígitos em linha mista (CBO "225135", nº de protocolo/exame) que colidam com
o token são destruídos do texto clínico. Além disso, a remoção é silenciosa
(sem telemetria no evento de extração, ao contrário do `RemovedLines` do
sarv3).

Premissa de domínio corrigida: **a marca quase nunca coincide com o nº de
ocorrência** — a coincidência era suposição que persistiu nas
implementações/fixtures, não regra observada. As fixtures canônicas atuais
(banda única do próprio nº) codificam o caso raro e migram para a forma
observada.

A comparação com o incidente real do ats-web (ajuste para marcas de 3–4
dígitos passou a remover anos de datas por nomeação por frequência) fixa o
boundary: este change **mantém o tamanho 5–6 dígitos** e endurece
precisão/recall dentro desse boundary — sem adotar nomeação por frequência
livre.

## What Changes

- **Nomeação unificada por linha de marca** (recall): linha de marca = linha
  composta **exclusivamente** de repetições do token de 5–6 dígitos (1 a N
  repetições — cobre banda cheia, diagonal com poucas repetições e linha
  solta). Token nomeado com **≥2 linhas de marca** no documento. Seguro em
  5–6 dígitos por construção (ano tem 4 dígitos; valores clínicos curtos não
  formam linha de marca).
- **Limiar ≥2 linhas** (precisão): linha única de repetições (ex.: valores
  iguais de tabela renderizados numa linha só) não nomeia sozinha. O dano de
  um falso positivo é limitado a linhas inteiras (item a seguir), o que
  permite manter o limiar baixo sem o risco do sweep destrutivo antigo.
- **Sweep residual assimétrico** (precisão): token nomeado **≠ nº de
  ocorrência** é removido apenas como linha de marca — ocorrências em linha
  mista são preservadas; token **== nº de ocorrência** (caso raro, mas
  possível) continua varrido globalmente (o valor sobrevive em
  `agency_record_number`, ordem D3 inalterada).
- **Separador explícito entre páginas**: `extract_document_text` concatena
  páginas com `"\n"` em vez de `""` — endurece a fronteira de página (o
  `get_text()` do PyMuPDF hoje sempre termina linha com `\n`, mas o contrato
  passa a ser explícito e não acidental).
- **Telemetria da remoção**: `strip_watermark` devolve relatório de contagens
  (tokens nomeados, linhas de marca descartadas, ocorrências varridas) e o
  evento `CASE_EXTRACTION_COMPLETED` passa a carregar essas contagens — só
  contagens, nunca valores de token.
- **Higiene e fixtures**: remoção do filtro redundante do passo 3 (`>= 1`);
  migração das fixtures canônicas de banda única do próprio nº para a forma
  observada (marca ≠ nº, ≥2 linhas de marca) e testes de regressão dos novos
  comportamentos.

Não muda: tamanho da marca (5–6 dígitos), ordem bruto→strip do nº de
ocorrência (D3/R5b), early-return que preserva texto sem marca byte-idêntico,
gate de regulação, fluxo/anonimização downstream (o nº citado inline segue
sendo tokenizado pela anonimização determinística, como já é hoje quando a
marca ≠ nº).

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `intake-nir`: a requirement **Extração assíncrona no cluster pdf** muda no
  comportamento de remoção da marca d'água — nomeação unificada por linha de
  marca (1..N repetições) com limiar ≥2 linhas, sweep de tokens ≠ nº de
  ocorrência restrito a linhas inteiras (preserva valores legítimos inline),
  separação explícita de páginas na concatenação e contagens da remoção no
  evento de extração.

## Impact

- `apps/intake/pdf_utils.py`: `strip_watermark` (assinatura passa a devolver
  texto + relatório), `extract_document_text` (join de páginas), padrões de
  nomeação.
- `apps/intake/tasks.py`: consumo do relatório e payload do evento
  `CASE_EXTRACTION_COMPLETED` (contagens).
- `apps/attachments/extraction.py` (impacto indireto, **sem edição**): reusa
  `extract_document_text` e decide local×OCR pelo limiar de 30 chars — o `\n`
  extra por página é coberto pela suíte de attachments na verificação do
  slice 001.
- `apps/intake/tests/test_pdf_utils.py` + `apps/intake/tests/test_tasks.py`:
  migração das fixtures canônicas (banda única do próprio nº → forma
  observada) e regressão dos novos comportamentos.
- Sem mudança de modelos/migrações/dependências; `anonymization`,
  `llm-pipeline` e gates downstream não são tocados (consomem
  `extracted_text` já limpo).
