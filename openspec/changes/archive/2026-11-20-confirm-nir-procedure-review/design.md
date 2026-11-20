# Design

## Context

Motivação e decisões de produto: `proposal.md`. Inspeção-base: HMD `f4aff1d`, referência somente-leitura `ats-web` `6191882`.

No HMD, `run_llm1_extraction` extrai com schema/prompts dos tipos declarados e grava `CaseProcedure.detection_status`; divergência retém `LLM_EXTRACTING`. `release_retained_case` chama `bypass_pipeline_divergence`, e o signal de `LLM_SUMMARIZING` com ator usuário retoma somente policy/prior/LLM2. A sumarização usa os declarados e já exige igualdade do conjunto de sugestões. O detalhe médico mostra badges, mas não a confirmação humana. `CaseEvent` possui payload JSON e tipos extensíveis sem migration; o painel exige cobertura dos tipos em `event_labels.py`.

A criação passa por `create_case_with_documents`, inclusive lote e reenvio corrigido (este último cria outro caso). Os workers compartilham locks/lease; o pipeline é disparado por `on_commit`. Não há recuperação específica de enqueue no HMD. Existem clusters nomeados `pdf`, `anonymization`, `llm` e `attachments`. Presidio e mapas do caso precedem qualquer LLM. A FSM tem 17 estados; o gate de formato é uma retenção diferente.

## Goals / Non-Goals

**Goals:**
- Separar observação automática (o texto sustenta quais procedimentos) de autoridade operacional humana (qual procedimento analisar).
- Tornar revisão, fonte e confirmação correlacionáveis e resistentes a POST obsoleto/duplo, worker atrasado e encerramento administrativo.
- Reaproveitar serviços/catalogação/locks HMD, sem acoplar domínio a views ou schemas de endoscopia.
- Dar provas TDD de manutenção e troca, inclusive exame sem detecção correspondente, até a fila e a leitura médica.

**Non-Goals:**
- Alterar thresholds, tornar recomendação bloqueio clínico, permitir múltiplos tipos na revisão ou transformar confirmação em evidência de indicação.
- Reextrair PDF, refazer anonimização sem mudança da fonte, reprocessar anexos ou exigir novo upload.
- Backfill de casos históricos, novas notificações, botão NIR de retry, recuperação geral de `FAILED`, novo app/estado/dependência.
- Proibir a consulta autorizada ao PDF original; a proibição é encaminhar o caso como pronto para decisão sem resumo e análise.

## Decisions

### D1 — Escopo prospectivo explícito sem migration de dados

Registrar `CASE_PROCEDURE_REVIEW_ENABLED`, payload `{version: 1}`, no mesmo atomic de criação em `create_case_with_documents`. O evento é marcador de contrato, não confirmação. Um helper de domínio consulta sua presença; não usar data de implantação configurável nem inferir versão pela existência de uma divergência. Lote e reenvio corrigido novo herdam a marca pela primitiva; o original encerrado não é marcado. Criação direta de fixtures precisa declarar explicitamente qual contrato simula.

Casos sem marcador não recebem novo form nem aceitam a nova ação. Permanecem no contrato anterior sem conversão, reprocessamento em massa ou promoção de bypass antigo a confirmação. A rota antiga de liberação continua para formato e para divergência histórica; rejeita divergência dos casos marcados. Isso implementa literalmente o recorte aprovado de novos envios, sem flag de rollout ou coluna nova. Remover o ramo legado não faz parte deste change.

Alternativas: timestamp/env exigiria configuração de implantação e fronteiras frágeis; coluna de versão exigiria migration sem necessidade; ativar todo caso retido contrariaria a decisão do dono.

### D2 — Confirmação no mesmo caso, com validação transacional

Novo serviço `apps/intake/services.py::confirm_case_procedure_review`, novo `ProcedureReviewForm`, POST próprio `intake:procedure_review_confirm`. Inputs: `procedure_type`, `review_acknowledged`, `review_justification`, `review_event_id`, `source_fingerprint`. Select único do catálogo com a mesma marcação `data-procedure-combobox` do upload; seleção atual pré-selecionada e confirmável. Não adicionar campos de upload.

GET não reserva o caso nem altera dados. No POST o serviço valida papel ativo `nir` e vínculo do ator ao papel, posse pelo criador, marcador D1, estado `LLM_EXTRACTING` + flag + `procedure_divergence`, ausência de decisão médica, tipo único válido, checkbox verdadeiro e justificativa após `strip` com 1–500 caracteres (rejeitar NUL). A view não é a única guarda. Caso alheio/inexistente = 404; papel errado = 403; form inválido ou precondição obsoleta = 400 com erro claro sem mutação; lock ativo de outro ator = 409. Reexibir seleção/justificativa escapadas e não marcar consentimento automaticamente.

O serviço delimita o commit real com `atomic(durable=True)` e não aceita um atomic externo de produção; a view fica fora de transação por requisição. Usar `select_for_update` no Case, revalidar revisão/fonte, adquirir lease curta de mutação no POST com as APIs existentes (contexto `nir_procedure_review`, papel `nir`), exigir sua posse e liberá-la no mesmo atomic do reprocessamento. Nenhuma chamada LLM dentro desse atomic. Duas submissões: a primeira limpa a flag/gera confirmação; a segunda encontra estado não elegível, sem outra confirmação nem outro enqueue. A aquisição da lease não pode fazer save de uma instância obsoleta; após claim, usar a instância fresca e liberar token antes do commit que dispara o worker. Não introduzir reserva longa/heartbeat do ats-web: o HMD não tem essa jornada NIR; row lock + claim curto oferecem a mesma segurança de mutação sem nova UX.

Alternativas: reaproveitar POST de bypass permitiria confirmação implícita; confiar só em hidden fields/view falharia em concorrência; criar outro caso confundiria a revisão com reenvio corrigido.

### D3 — Autoridade versionada e fonte verificável

Novo módulo `apps/cases/procedure_review.py`: elegibilidade, revisão mais recente por `(timestamp,id)`, fingerprint, validação/consumo da confirmação e projeção de aviso. Enum e rótulos legíveis para:

| Evento | Ator | Payload principal |
|---|---|---|
| `CASE_PROCEDURE_REVIEW_ENABLED` | sistema | `version` |
| `CASE_PROCEDURE_REVIEW_CONFIRMED` | usuário/papel nir | `version`, `review_event_id`, `previous_declared_procedures`, `confirmed_procedures` (singleton), `selection_changed`, `justification`, `source_fingerprint`, `anonymized_text_digest`, `pseudonym_map_digest` |
| `CASE_PROCEDURE_REPROCESSING_REQUESTED` | usuário/papel nir | `confirmation_event_id` |
| `CASE_PROCEDURE_REVIEW_APPLIED` | sistema | `confirmation_event_id`, `automatic_procedures` (detecção real da nova extração), `effective_procedures` (singleton confirmado) |
| `CASE_PROCEDURE_REVIEW_INVALIDATED` | sistema | `confirmation_event_id`, `reason` |
| `CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED` | sistema | `confirmation_event_id`, `recovery_scheduled`, código técnico sem PHI |

O evento de revisão referenciado é `CASE_GATE_PROCEDURE_DIVERGENCE`. Identificar o evento atual, não confiar em motivo enviado pelo navegador. SHA-256 versionado inclui documentos principais em ordem (ids/posições e hash dos bytes) e `extracted_text`, com serialização não ambígua; unreadable/missing = indisponível, nunca hash de vazio como autorização. Não incluir anexos, pois não dirigem esta análise, nem logs dos bytes/textos. Mesmo nome de arquivo com bytes diferentes invalida. Para casos marcados, a primeira LLM1 captura antes do dispatch e revalida antes de persistir o fingerprint da fonte, o SHA-256 de `anonymized_text` e o SHA-256 da serialização JSON canônica de `pseudonym_map` (chaves ordenadas e separadores fixos, sem normalizar valores); grava os três no evento de conclusão e na revisão resultante, nunca os textos/valores do mapa. GET/POST de confirmação exige que fonte, cópia anonimizada e mapa continuem correspondendo à revisão vista, além de validar o fingerprint enviado no form. Os digests da confirmação vêm do snapshot confiável do servidor, não de campos livres do navegador. Isso impede que um GET novo legitime texto anonimizado de uma fonte alterada por fora do fluxo. O mapa/tokenização é preservado; cópia anonimizada vazia ou com digest divergente exige recuperação segura, nunca dispatch obsoleto (D4).

Confirmação mais recente só vale se versão/ator/papel registrado, singleton catálogo, declaração corrente, revisão corrente, `source_fingerprint`, `anonymized_text_digest` e `pseudonym_map_digest` coincidirem, **e não existir `CASE_PROCEDURE_REVIEW_INVALIDATED` correlacionado ao seu `confirmation_event_id`**. A invalidação é definitiva para aquele evento: restaurar declaração/fonte/digests não o revalida; nunca buscar confirmação antiga. Se já invalidada, reutilizar o motivo registrado para o desfecho de D3, sem emitir outro evento apenas por revalidá-la; nova autoridade exige outra confirmação explícita vinculada à revisão vigente. Comparar todos os digests antes de escolher o ramo: problema de integridade tem precedência mesmo se revisão/declaração também mudou. Em toda revalidação, sobretudo após chamada longa, reler primeiro o estado: **`CLEANED` aborta antes de checar digests, registrar `INVALIDATED` ou tentar `FAILED`**, inclusive se a minimização tiver apagado os campos. Para caso não encerrado, falha nunca recupera confirmação antiga; o desfecho depende do motivo:

- `source_changed`, `source_unavailable`, `anonymized_mismatch`, `anonymization_missing` ou `pseudonym_map_mismatch`: no worker, auditar `INVALIDATED` uma vez por confirmação/motivo, transicionar para **`FAILED` operacional**, sem nova chamada externa nem avanço, e exigir recuperação supervisionada da cadeia de extração/anonimização. Isso vale mesmo se uma detecção anterior coincidir com o declarado. Se a inconsistência surgir durante chamada já em voo, não persistir seu resultado nem disparar a etapa seguinte. `CLEANED` continua abort benigno, sem ressuscitar caso ou tentar transição para FAILED.
- `review_superseded` ou `declaration_changed`, com fonte, texto anonimizado e mapa íntegros: auditar invalidação e voltar ao gate automático; somente a reconciliação automática atual pode seguir sem confirmação se não houver divergência. Job atrasado associado a confirmação diferente da vigente é no-op, não executa esse fallback nem cria revisão adicional.
- Payload, versão, ator ou seleção inválidos: rejeitar autoridade e falhar fechado como erro operacional `FAILED`, sem dispatch; não inferir consentimento.

GET continua somente leitura. POST obsoleto por integridade é recusado com 400, sem criar confirmação ou job nem mutar o caso, e orienta suporte (reabrir a tela não legitima nova fonte). Já uma revisão obsoleta com fonte íntegra permite reabrir e revisar novamente. O desfecho FAILED acima é do processamento/worker, não um efeito escondido de GET ou de formulário rejeitado. Não confundir `CASE_GATE_BYPASSED` ou `CASE_PROCEDURES_DECLARED` com confirmação. Semântica de `APPLIED`: `automatic_procedures` é exatamente o conjunto de tipos realmente detectados **na nova passada LLM1**, sem duplicatas e na ordem canônica do catálogo, admitindo `[]`; não é a união declarado×detectado, a lista de conflitos nem o conjunto efetivo. `effective_procedures` é o singleton confirmado. A detecção original vem do `CASE_GATE_PROCEDURE_DIVERGENCE` indicado por `CONFIRMED.review_event_id`, considerando somente classes `match` e `missing_declaration` de seu payload `classification` (não `not_detected`). `APPLIED` é idempotente por confirmação + resultado automático/efetivo para não duplicar em reexecução equivalente. A detecção real permanece nas rows e no artefato; ela **não é sobrescrita por `detected` só porque o NIR confirmou** (adaptação intencional ao HMD para preservar badges verdadeiros).

Justificativa livre existe apenas na auditoria/UI interna, com escaping. Nunca vai ao LLM, inclusive como instrução; o contexto técnico só inclui código confirmado e proveniência sem PHI.

### D4 — Nova extração real, sem novo estado ou nova fonte

Adicionar operação FSM nomeada `reprocess_after_procedure_review`: self-transition `LLM_EXTRACTING -> LLM_EXTRACTING`, com `CASE_PROCEDURE_REPROCESSING_REQUESTED` referenciando a confirmação. Somente o serviço D2 a usa após revalidar tudo. Não usar o antigo caminho `LLM_SUMMARIZING` como confirmação nova.

No mesmo atomic: preservar fontes/anonimização/mapa/metadados/documentos/anexos; trocar declaração via serviço existente mesmo se a seleção for igual; limpar `structured_data`, `summary_text`, `policy_result`, `suggested_action`; resetar detecção das rows a `pending` (não apagar rows/história); manter decisões intocadas pois qualquer decisão torna a ação inelegível; limpar flags de retenção; gravar confirmação e pedido FSM; liberar lease. Não tocar tempos originais de recebimento. Falha reverte tudo.

Após commit, o worker reexecuta LLM1 com schema/prompts do singleton confirmado sobre `anonymized_text` preservado, depois policy/prior/LLM2. Fonte, texto anonimizado ou mapa inconsistentes seguem o desfecho FAILED de D3, nunca o gate automático; caso `CLEANED` aborta sem transição. Nenhum deles autoriza novo envio externo/avanço; não reanonimizar silenciosamente uma fonte diferente. A API normal não troca documento nesta retenção; uma troca externa exige nova passagem segura pela cadeia de extração/anonimização na recuperação operacional.

`run_llm1_extraction` precisa conhecer uma confirmação válida **antes de gravar retenção**. Extrai/detecta/reconcilia normalmente, persiste evidência e detecção reais, mas com autoridade vigente registra `APPLIED` e não recria flag/evento de retenção pela divergência. O orquestrador também distingue divergência observada de bloqueio: não basta mudar só um deles. Nunca ignorar falha de schema/idioma/tokenização. Sem confirmação existente, ou com invalidação apenas por revisão/declaração e integridade preservada, comportamento automático permanece; qualquer quebra de integridade ou autoridade malformada segue FAILED operacional de D3.

Entrada inicial por anonimização e pedido de reprocessamento têm triggers distintos em `apps/pipeline/signals.py`, ambos pós-commit. Self-transition de início do worker não dispara outro job. Não disparar recuperação duas vezes via signal e chamada explícita do serviço.

Alternativas: reutilizar extração anterior perde dados específicos do novo perfil; mandar LLM1 afirmar o exame confirmado fabricaria evidência; setar detecção humana como automática ocultaria a contradição.

### D5 — Analisar o confirmado sem inventar suporte clínico

O singleton confirmado e já declarado dirige schema/prompts LLM1, policy da seção, prior-case, view LLM2, igualdade exata da resposta e matriz de subtipo médico. LLM1 continua listando em `pedido.procedimentos_solicitados` apenas o que o relatório sustenta. Dado específico ausente continua `nao_informado`/`incerto`; campos comuns sustentam a análise possível. A view filtrada LLM2 não injeta item/evidence-span para fingir presença do confirmado.

Acrescentar contexto técnico separado nos prompts **renderizados** da LLM2: procedimento confirmado após revisão humana, sem equivalência a indicação/aprovação clínica, usar dados comuns e explicitar ausências. Não alterar os prompts versionados de todos os perfis apenas para transportar esse estado; a lógica vive no serviço, testada por captura de payload. O schema e a precedência da policy permanecem; recomenda recusar não bloqueia decisão médica. Sugestão deve cobrir exatamente o singleton, mesmo que `pedido` não o contenha.

### D6 — Completude como pré-condição do encaminhamento

Antes de `complete_llm_summarization`, sob row lock final e alinhado à mesma execução/confirmacão: exigir extração estruturada válida, `summary_text.strip()` não vazio, policy e sugestão para exatamente o conjunto efetivo, agregado válido e nenhuma retenção pendente. Nos novos casos, a operação pública final também rejeita ausência desses artefatos; o orquestrador traduz erro em `FAILED`. O predicado estrutural de completude deve viver em `apps/cases/procedure_review.py`, sem importar pipeline, LLM ou anonimização; validação de schema detalhada continua no pipeline. Assim `cases` não passa a depender dos apps consumidores. Ajustar validação LLM2 para rejeitar resumo só de espaços. Limpar derivados em D4 impede que artefatos da declaração anterior satisfaçam a guarda.

Não filtrar filas por presença de resumo como substituto de corrigir o produtor (isso esconderia falhas). Fila continua por estado, e nunca deve receber evento `AWAITING_DOCTOR` sem completude. GET/POST de trabalho médico dos novos casos valida completude como defesa contra inconsistência e não oferece decisão sobre relatório puro; detalhe autorizado e PDF podem continuar consultáveis com estado de erro/andamento. Casos históricos mantêm seu contrato, sem saneamento retroativo.

### D7 — Recuperação pós-commit de enqueue, não retry de análise

Um receiver para `CASE_PROCEDURE_REPROCESSING_REQUESTED` agenda `on_commit(..., robust=False)` e chama wrapper de enqueue seguro em `apps/pipeline/tasks.py`. O callback executa na saída do atomic durável de D2, antes do retorno do serviço à view; não existe retorno de outcome via signal nem segundo enqueue no serviço/view. Primeiro tentar `enqueue_case_pipeline` existente; exceção de dispatch não desfaz confirmação commitada. Programar um `django_q.models.Schedule.ONCE` para **`apps.pipeline.orchestrator.process_case_pipeline`**, no cluster **`llm`**, após 60 s, com nome baseado em caso+confirmação; argumentos serializados incluem string UUID e `confirmation_event_id` esperado. O entry point recebe esse segundo argumento opcional, normaliza UUID e rejeita job de confirmação diferente da vigente, sem interferir nas entradas iniciais que não o fornecem. Não copiar o recovery do ats-web pelo worker PDF: no HMD o PDF worker não é a retomada correta.

Deduplicar recovery pendente por caso+confirmação, sem nome determinístico residual após execução ONCE. O entry point só age em estado de pipeline/versão/revisão válidos, sob a lease; fora disso é no-op. A confirmação disparada identifica a execução na validação do resultado; schedules antigos não restauram declaração/artefatos anteriores. Reexecução não repete efeitos já concluídos; chamadas externas podem se repetir se houve interrupção antes da persistência (não prometer exactly-once de rede).

Contrato do outcome: `ProcedureReviewEnqueueAfterCommitError`, definido em `apps/pipeline/tasks.py`, carrega `case_id`, `confirmation_event_id`, `recovery_scheduled: bool` e `reason_code` sanitizado. O **callback** levanta essa exceção após tratar a falha de dispatch; ela propaga pela saída do serviço de confirmação, cujo commit já ocorreu, e somente a view de confirmação a captura. Não usar `robust=True` nem um `except` genérico que a converta em sucesso ou rollback da confirmação.

Antes de levantar, o wrapper abre **nova transação própria** para recovery e `CASE_PROCEDURE_REPROCESS_ENQUEUE_FAILED`: tenta gravar o Schedule em savepoint, registra o outcome do scheduling no evento mesmo se essa tentativa falhar e commita a auditoria. Se a transação de recovery/auditoria inteira falhar, nenhum Schedule desse atomic é considerado programado: propagar a exceção tipada com `recovery_scheduled=False` e log técnico sanitizado, sem alegar auditoria persistida. Nada disso reverte o atomic já commitado da confirmação.

A view distingue: enqueue normal → redirect 302 de sucesso; exceção com `recovery_scheduled=True` → redirect 302 ao detalhe com **aviso**, «Confirmação registrada; recuperação do processamento programada»; exceção com `False` → detalhe de erro **HTTP 503**, «Confirmação registrada; processamento pendente de suporte», sem formulário/botão de repetição e sem sucesso falso. O evento de falha contém só código e outcome, nunca mensagem livre da exceção, justificativa ou fonte. Exigir testes HTTP `transaction=True` do callback real, com estado/evento/Schedule consultados após a resposta; callbacks manualmente simulados não provam esse canal.

Processamento que foi executado e falhou segue `FAILED`, sem recuperação automática infinita nem botão para NIR. A recuperação manual continua operada pela equipe; não criar transição genérica de `FAILED` neste change.

### D8 — Evidência médica e auditoria sem novo poder clínico

Presenter usa `APPLIED` + confirmação referenciada, nunca só metadata mutável ou o último evento de um tipo sem ligação. Aviso visível no detalhe e na página de decisão, junto aos procedimentos e antes do formulário: exame confirmado; mantido/trocado e declaração anterior; detecção automática da revisão original e, se diferente, detecção da nova passada (incluindo nenhum detectado); nome do NIR/data/justificativa; «A confirmação do NIR não é aprovação clínica; a decisão permanece com o médico». O resumo continua clínico, e o aviso é determinístico fora do texto do LLM. Para não confundir os dois instantes, a detecção original é reconstruída da revisão referenciada pela confirmação e a detecção nova é `APPLIED.automatic_procedures` conforme D3; a classificação de conflito do ats-web não substitui esse campo no HMD.

Só o singleton confirmado é objeto de policy/prior/decisão. Rows extras de detecção seguem visíveis como histórico/observação, não como decisões adicionais. A matriz de acesso lê declaração atualizada: trocar para outro subtipo transfere o acesso como já prevê `DoctorAccess`. Preservar reidentificação apenas na renderização e escape do texto livre.

Mapear novos eventos no painel e tornar seus payloads legíveis com mantido/trocado, conjuntos e justificativa, em vez de JSON cru; o painel já é a trilha autorizada. Não adicionar trilha inteira ao detalhe NIR/médico nem notificações. Atualizar manual existente. Não expandir o card «Sumário apresentado ao médico» do painel para novos blocos: auditoria da confirmação vive na trilha.

### D9 — Prova de navegador com LLM determinístico exclusivo de verify

O harness atual usa LLM inline, chave externa vazia e seeds que avançam a FSM diretamente; isso não prova a nova jornada. O slice 002 deve entregar o adaptador **`config/llm_verify.py`**, sua factory `create_verify_llm_client` e o override de `LLM_CLIENT_FACTORY` **apenas em `config/settings/verify.py`**. Não tocar a factory padrão de base/dev/test/prod. O adaptador recusa importação/instanciação/uso fora da identidade positiva `config.settings.verify`, incluindo settings programáticas ou env divergente, e exige o banco isolado `hmd_verify_*`, pelo padrão fail-closed de `backends_verify`. Não depende de chave OpenRouter e nunca abre rede ou faz fallback ao cliente real.

Respostas LLM1/LLM2 válidas para o schema solicitado são determinadas por cenários sintéticos explicitamente reconhecidos e estágio/perfil da chamada, não por contador global ou sequência em memória (novas requisições/workers podem criar novo cliente). Cenários mínimos: manter apesar de detecção divergente repetida; trocar para procedimento não detectado; detecção vazia; falha tipada do cliente. Tanto a falha simulada quanto a entrada não reconhecida levantam **`apps.pipeline.llm.LlmError`**, com `kind` já existente e mensagem genérica sem PHI, nunca exceção genérica ou resposta de sucesso fabricada. Isso preserva o protocolo do cliente e o tratamento tipado de consumidores como anexos. O fake substitui somente o transporte LLM: não pode gravar eventos, confirmar caso, mudar FSM, executar policy ou pré-calcular `APPLIED`.

Preparar fontes PDF e fixtures novas por opção explícita **`seed_verify --procedure-review`**, preservando os seeds padrão `VERIFY-001/002`. Antes de criar qualquer caso desse modo, a opção chama o comando existente **`seed_prompts`**, idempotente, para assegurar templates ativos sem sobrescrever conteúdo/versões existentes; não presumir que `migrate` ou o seed padrão os criam. Em seguida, o setup passa pela primitiva de criação e pipeline reais com o fake para obter retenção inicial coerente (fonte, anonimização, digests e marcador). Em seguida, login e **POST real do navegador** produzem confirmação, `APPLIED`, reprocessamento e artefatos. Não semear esses resultados finais nem usar endpoint de mutação/cookie injetado. Adaptador de settings não é helper CLI: drives continuam shell/Node e setup Python restrito ao comando existente, com as guardas de banco/media/marker e cleanup do harness preservadas.

Identificadores sintéticos estáveis das fixtures opcionais (nº de ocorrência numérico extraível do PDF): `990101` = manter, `990102` = trocar para não detectado, `990103` = detecção vazia, `990104` = falha tipada na LLM2 após confirmação (a primeira LLM1 ainda gera retenção). Preservar esses números nos drives; não reutilizar `VERIFY-001/002`. A opção é idempotente na mesma run: repetir setup não duplica caso/documento/eventos nem reseta decisão, confirmação ou progresso de fixture já criada. Caso existente com o identificador mas incompatível com a fixture esperada deve gerar erro explícito, não ser sobrescrito. Em banco novo ou após cleanup, os mesmos números podem identificar novas fixtures da nova run. Testar execução duas vezes antes e depois de uma confirmação e verificar preservação também das versões de prompts.

Blast radius adicional aprovado para 002: `config/llm_verify.py`, `config/settings/verify.py`, `apps/accounts/management/commands/seed_verify.py`, `tests/test_llm_verify.py` e testes de guardas/seed em `apps/accounts/tests/`, além da jornada/mapa em `.pi/skills/verify-hmd/`. O slice 004 **reutiliza** a factory e estende somente cenários/fixtures/drive estritamente necessários. Provas obrigatórias: respostas válidas, determinismo entre instâncias, cenário desconhecido rejeitado, zero transporte HTTP e subprocessos recusando uso em dev/test/prod/settings programáticas. A prova visual de 002/004 percorre o servidor real sem chamadas externas; testes pytest continuam cobrindo a mesma cadeia. Nenhuma dessas mudanças de código é feita durante o planejamento.

## Risks / Trade-offs

- [Confirmação aplicada em só uma das duas camadas] → testes que repetem a mesma detecção e verificam flag, eventos e chegada real à fila; cobrir LLM1 e orquestrador juntos.
- [Arquivo grande/read error no fingerprint] → leitura local em chunks/hash, erro fechado e nenhum byte/texto em log; sem upload ou rede sob row lock.
- [Troca de fonte/texto anonimizado/mapa durante LLM] → revalidar todos os digests e autoridade antes de dispatch/persistência; inconsistência termina FAILED sem novo dispatch nem resultado em voo. `CLEANED` aborta sem ressuscitar, e a lease é liberada no commit final.
- [Fake de verificação fora do perímetro] → override somente em verify, guarda de identidade positiva/banco isolado, negativos em subprocesso e transporte HTTP bloqueado; nunca fallback a cliente real ou uso em dev/prod.
- [Falha dupla de enqueue e Schedule] → confirmação durável, erro/auditoria visíveis e instrução operacional; não há garantia de conclusão sem infraestrutura funcionando.
- [Scheduler não consome cluster nomeado] → testar `cluster='llm'` no Schedule e smoke operacional com qcluster real antes de liberar intake; mock isolado não prova execução.
- [Logs/modelo expõem justificativa] → teste canário com nome/CPF na justificativa e zero ocorrência em payload LLM/logs de falha.
- [Migração silenciosa de casos antigos] → marcador somente na criação, testes de caso sem marcador e de reenvio novo sem alterar original.
- [Blast radius do guard de completude] → limitar contrato novo por D1, ajustar fixtures que simulam casos marcados e não reescrever testes históricos para esconder regressão.

## Migration Plan

1. Implementar slices TDD, sem migração/backfill ou novas variáveis. Não criar marker para casos já existentes. Pausar intake/workers durante troca de versão para não misturar criação e workers antigos.
2. Implantar web e workers da mesma revisão; validar cluster `llm` e scheduler, fontes/anônimos íntegros e fluxo sintético manter/trocar até médico antes de habilitar novos envios.
3. Rollback operacional: desabilitar intake e parar/drain de workers novos antes de reverter. Não permitir que workers antigos processem casos marcados pendentes (eles não entendem a autoridade/completude nova); restaurar versão compatível ou resolver em recuperação supervisionada. Não apagar eventos de confirmação.
4. Na conclusão do change, sincronizar deltas, atualizar `PROJECT_CONTEXT.md` e arquivar somente após gate e aceitação. Este estágio cria apenas planejamento.
