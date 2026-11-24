# Proposal

## Why

Reenvios sucessivos de pedidos ainda nas filas médica e de agendamento criam trabalho duplicado e podem reiniciar artificialmente a espera. Algumas unidades geram outra regulação para o mesmo pedido; o prior-case atual depende do número ou de nascimento, ausente no relatório SESAB.

## What Changes

- Identificar reapresentações por nome normalizado e regulação, CNS do paciente ou unidade de origem, sempre do mesmo procedimento; idade é evidência auxiliar, não obrigatória. CNS vem exclusivamente do bloco textual SESAB completo definido em D1, validado na extração real das amostras; formatos parciais deixam CNS vazio sem bloquear o caso.
- Evidência forte permite resolução automática; nome + unidade, idade conflitante, identificadores conflitantes ou ambiguidade exigem conferência pelo NIR, sem bloqueio por falta de resposta.
- Oferecer exatamente duas respostas no detalhe NIR, com comparação dos dados e acesso aos dois PDFs: mesmo pedido ou pedido independente.
- Na fila médica, substituir o anterior somente quando o novo estiver integralmente processado; preservar espera acumulada sem reescrever a data de criação.
- Na fila de agendamento, preservar original, aprovação e andamento; encerrar novo vinculado somente após LLM1/reconciliação vigente (dispensando LLM2), contar reapresentações e disponibilizar seus PDFs/anexos separadamente do relatório aprovado, inclusive no Histórico do Agendador em CLEANED retido.
- Excluir casos encerrados por reapresentação de Em andamento e da tabela por procedimento (Total/Pendentes e demais disposições), preservando volume de envios no Total do resumo, encerrados técnicos e auditoria.
- Negativa médica anterior informa novo ciclo dentro de janela única configurável de 15 dias; casos ainda nas filas não têm limite de dias.
- Não criar triagem de atualização clínica, suspensão do agendamento ou revisão médica decorrente do reenvio. Risco de atualização percebida apenas no retorno à origem explicitamente aceito pelo dono.

## Capabilities

### New Capabilities

- `case-reappearance`: identificação, confirmação, vínculo e resolução de reapresentações de pedidos.

### Modified Capabilities

- `llm-pipeline`: prior-case com identidade ampliada e janela unificada.
- `doctor-decision`: prior-case consistente com o pipeline e espera herdada nas substituições.
- `scheduling`: contador, documentos recebidos e exceção estreita ao gate de acesso a PDFs vinculados.
- `case-closure`: encerramento específico por reapresentação e proteção temporária contra minimização/purga de documentos ligados a caso ativo.
- `pdf-original-viewer`: autorização específica dos documentos de reapresentações, sem abrir o PDF original pré-agendamento ou em CLEANED.
- `dashboard`: excluir encerramentos técnicos de reapresentação das contagens de demanda sem apagar o volume de envios e a auditoria.

A interface NIR é especificada na nova capacidade, sem alterar os contratos existentes de lote, gate e confirmação de procedimento em `intake-nir`.

## Impact

Apps cases/intake/anonymization/pipeline/doctor/scheduler/dashboard, templates, settings, compose de web/workers, testes e manual. Migrações aditivas para CNS, espera herdada e vínculos/revisões de reapresentação; nenhuma nova dependência ou estado FSM. Nova transição sistêmica auditada para CLEANED, distinta do encerramento administrativo. O gate atual do PDF original do agendador permanece: acesso antecipado é somente à documentação recebida em reapresentações efetivamente vinculadas.

Implementação não autorizada por esta proposta. Não executar backfill destrutivo nem resolução retroativa em massa; históricos permanecem candidatos quando seus dados existentes permitem identificação segura.
