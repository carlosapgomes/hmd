# Proposal: attachment-processing-ocr

## Problema

Anexos de evidência (fotos/scans de exames e PDFs extras) não existem no HMD:
o intake aceita apenas os PDFs do relatório principal (`CaseDocument`,
PDF-only). O plano §9 exige **OCR híbrido** (PDF com camada de texto →
PyMuPDF local; foto/scan/PDF-imagem → `VISION_MODEL` externo — **anuência
formal do dono para PII sair do hospital apenas nesses casos**, respostas2
H6/H7), **anonimização do texto do anexo antes de qualquer LLM**, e
**verificação de identidade do paciente** (`patient_match:
match|mismatch|unknown`) exibida como card na decisão médica — mismatch é
alerta e **nunca** descarta automaticamente. Além disso, o fechamento do
change 09 não limpa anexos (eles não existiam).

## Objetivo

1. **Upload e listagem**: NIR anexa arquivos (jpg/png/pdf) ao criar o caso (e
   no reenvio corrigido); anexos ficam visíveis no detalhe do NIR com status.
2. **Extração híbrida assíncrona**: novo app `apps/attachments` com worker no
   cluster `attachments`; processamento dispara após a anonimização do
   relatório principal (paralelo ao pipeline LLM); PDF com texto → PyMuPDF;
   foto/scan/PDF-imagem → `VISION_MODEL` (OpenRouter) com **evento de
   auditoria**; falha → status `failed`, caso **nunca** bloqueado.
3. **Anonimização + verificação**: texto extraído → Presidio com **mesmo
   espaço de tokens do caso** (entidades convergem) → LLM de verificação
   **só vê tokens**: "isto é exame/relatório? corresponde ao paciente
   `<PESSOA_N>`?" → `patient_match` + resumo + evidence persistidos na row +
   `CaseEvent`.
4. **Cards na decisão médica**: seção "Anexos" no presenter re-identificado
   (badge match/mismatch/unknown + resumo/evidence re-identificados na
   renderização); mismatch = alerta, decisão segue com o médico.
5. **Fechamento íntegro**: a ciência do NIR (change 09) passa a remover
   também anexos (rows + arquivos físicos) — spec `case-closure` MODIFIED.

## Escopo

**Inclui**: app `apps/attachments` (model `CaseAttachment`, migration 0001 do
app, extração/worker/signals, verificação); kwargs aditivos no
`create_case_with_documents`/`create_corrected_resubmission`; prompt seed
`ATTACHMENT_VERIFICATION` (29º); settings (`VISION_MODEL`, cluster
`attachments`, `ATTACHMENTS_RUN_TASKS_INLINE`, limites); seção de anexos no
doctor; integração com `acknowledge_case_receipt`; spec nova `attachments` +
delta MODIFIED de `case-closure`.

**Não inclui**: upload suplementar pós-criação (fase "supplemental" do
ats-web — fora do pedido); supressão auditável de anexo (ats-web); OCR local
Tesseract (alternativa local fica registrada no plano de risco, não
implementada); dashboard (11); exibição de imagem do anexo para o médico
(cards textuais; o PDF original já é acessível ao doctor via PDF do caso —
anexos NÃO entram nessa rota).

## Sucesso

- NIR anexa 1 foto + 1 PDF na criação; após a anonimização do relatório os
  anexos são processados (foto → OCR externo com evento de auditoria; PDF
  com texto → local), anonimizados e verificados sem que nenhuma chamada LLM
  recebe dado real.
- O médico vê os cards com badge e resumo re-identificado; mismatch aparece
  como alerta e o caso segue decidível.
- Falha de visão/LLM deixa o anexo `failed` visível e o caso intacto.
- Ciência do NIR remove anexos (rows/arquivos) junto ao resto da limpeza.

## Capabilities

- `attachments` (nova) — `specs/attachments/spec.md` (ADDED).
- `case-closure` (MODIFIED) — `specs/case-closure/spec.md` (limpeza inclui
  anexos; todos os cenários existentes preservados + 1 novo).
