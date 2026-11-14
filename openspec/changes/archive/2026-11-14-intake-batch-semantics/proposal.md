# Change: intake-batch-semantics

## Why

Decisão do dono (2026-09-14, alinhando ao ats-web antes da fase 2): no HMD
original assumiu-se "N PDFs = partes de UM relatório de um caso" — mas a
semântica correta do serviço é **cada PDF = um relatório de um paciente**.
Com a semântica atual, um envio com 3 PDFs de 3 pacientes produziria **um
caso contendo 3 pacientes** (uma anonimização, um pipeline, uma decisão) —
clinicamente errado. Nunca apareceu porque a fase 1 está com intake
desligado; precisa ser corrigido ANTES da fase 2.

Regras do dono (= ats-web): (1) cada PDF do envio vira um **caso
independente**; (2) **um único tipo** de procedimento por envio/lote — não
há múltiplos tipos; (3) **anexos somente quando o envio tem exatamente 1
PDF** (impossível ligar múltiplos relatórios a múltiplos anexos com
segurança); (4) **falhas parciais**: arquivo inválido rejeita só ele, casos
dos válidos são criados com os erros listados; (5) limites de lote
(contagem/tamanho por arquivo/tamanho total) descritos na UI.

## What Changes

- Novo serviço `submit_report_batch`: validações de lote → tipo único →
  anexos (regra do pdf_count) → loop por arquivo criando um caso cada
  (1 documento + tipo do lote); retorna `(cases, errors)` no molde ats-web.
- `create_case_with_documents` vira a primitiva "um arquivo → um caso"
(usada pelo lote e pelo reenvio); `procedure_types` (lista) → `procedure_type` (único).
- `validate_attachments` ganha `pdf_count`: `pdf_count != 1` → erro nomeado
(não-bloqueante no lote: casos são criados SEM anexos + erro informado;
com 1 PDF, anexo inválido aborta tudo — molde ats-web).
- Form: `procedure_type` escolha ÚNICA (radio) para o lote; hints com os
números reais dos settings; **campo de anexos desabilitado via JS** quando
>1 arquivo selecionado (+ validação server); template de resultado do lote
(casos criados + erros por arquivo).
- Reenvio corrigido: **exatamente 1 PDF** + anexos permitidos + tipo único.
- Settings: `INTAKE_MAX_FILES_PER_BATCH` (30), `INTAKE_MAX_UPLOAD_BYTES_PER_FILE`
(20 MB), `INTAKE_MAX_UPLOAD_BYTES_PER_BATCH` (100 MB — Cloudflare limita
request body ~100 MB; ats-web usa 600 MB sem túnel); `INTAKE_MAX_DOCUMENTS`
é substituído.
- Specs: `intake-nir` REMOVED+ADDED (semântica do lote) e `attachments`
MODIFIED (upload só com exatamente 1 PDF).

## Impact

- **Specs**: `intake-nir` (1 REMOVED + 1 ADDED), `attachments` (1 MODIFIED).
- **Código**: `apps/intake/{services,forms,views}.py`, template
  `templates/intake/home.html`, JS novo, settings, `.env.example`,
  `docker-compose.prod.yml`, testes (serviço/form/view/reenvio).
- **Risco**: médio — muda a semântica central do intake e a assinatura
interna da criação; mitigado por TDD no serviço antes da UI e por a fase 1
estar desligada (sem dados em produção). Sem migrations (modelo de documentos
permanece; cada caso passa a ter exatamente 1).
