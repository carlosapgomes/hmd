# Proposal

## Why

O HMD apresenta a divergência entre procedimento declarado pelo NIR e detectado pelo LLM, mas permite apenas dispensá-la mantendo a declaração e reaproveitando a extração. Precisamos reproduzir o comportamento funcional do `ats-web`: confirmação humana explícita, com manutenção ou troca do exame, reprocessamento completo e contradição claramente apresentada ao médico, sem encaminhar um relatório sem resumo e análise.

## What Changes

- Nos novos casos enviados após a implantação, substituir o bypass de divergência por revisão com seleção de **um único procedimento entre os 13** do catálogo, inclusive não detectado pelo LLM.
- Exigir justificativa de 1–500 caracteres úteis e confirmação explícita de leitura, mesmo para manter o exame original; validar autorização, revisão e fonte sob transação.
- Registrar confirmação humana autoritativa e reprocessar o **mesmo caso** desde LLM1, com o perfil confirmado, seguido de policy, prior-case e LLM2. Preservar PDF, anexos, texto extraído, anonimização e história.
- Impedir loop pela mesma divergência enquanto a confirmação permanecer válida; preservar a detecção automática real, sem fabricar consenso ou evidências clínicas. Fonte, texto anonimizado ou mapa divergentes no worker causam `FAILED` operacional sem novo envio externo, não retorno permissivo ao gate automático.
- Apresentar ao médico resumo e análise do procedimento confirmado, com aviso de confirmação versus detecção automática, autor, data, justificativa e ressalva de decisão clínica exclusiva do médico.
- Preservar confirmação já commitada se o enqueue falhar, programar recuperação automática e tornar a falha observável. Falha de processamento permanece `FAILED`, sem fila médica nem botão novo de tentativa para o NIR.
- Não converter casos anteriores nem reinterpretar bypasses antigos como confirmação. Não modificar o gate de formato SESAB, o catálogo, os critérios clínicos ou o fluxo pós-decisão.

## Capabilities

### New Capabilities

Nenhuma. A funcionalidade estende os limites existentes.

### Modified Capabilities

- `case-management`: proveniência autoritativa da confirmação, auditoria append-only e operação FSM de reprocessamento sem estados novos.
- `intake-nir`: revisão explícita da divergência com escolha única, consentimento, justificativa e preservação do caso/fonte.
- `llm-pipeline`: reextração, conjunto efetivo confirmado, prevenção de loop, completude antes do encaminhamento e recuperação de enqueue.
- `doctor-decision`: aviso consultivo da contradição e análise correspondente ao exame confirmado, mantendo guards por subtipo.

## Impact

- Código previsto: `apps/cases/` (eventos, operação FSM, helpers de revisão/proveniência e declaração), `apps/intake/` (serviço, form, rota e detalhe), `apps/pipeline/` (LLM1/LLM2, orquestração e disparo), `apps/doctor/` (presenter), templates NIR/médico, rótulos de auditoria e manual existente.
- Verificação: adaptador LLM determinístico exclusivo de `config.settings.verify`, fixtures sintéticas no `seed_verify` e provas de isolamento/zero rede para tornar executáveis as jornadas reais de navegador, sem alterar clientes dev/prod.
- Reutilizar django-q2/PostgreSQL, os 17 estados, a infraestrutura de locks, o combobox e a anonimização Presidio fail-closed; sem dependências novas, API REST ou cópia dos schemas de endoscopia do `ats-web`.
- **Mudança de contrato nos novos casos:** POST de bypass simples não resolve divergência; somente a confirmação explícita válida dispara reprocessamento. Formato SESAB e casos antigos mantêm seus contratos.
- Sem migração retroativa de dados, sem reenvio obrigatório de arquivos, sem multiprocedimento, notificações novas ou nova interface de recuperação.
- Referência inspecionada: HMD `f4aff1d`; `ats-web` `6191882`, especialmente `apps/intake/services.py::confirm_case_procedure_review`, `apps/cases/procedure_review.py` e `apps/pipeline/orchestrator.py` (pipeline v4). Replicar comportamento, não implementação literal.
