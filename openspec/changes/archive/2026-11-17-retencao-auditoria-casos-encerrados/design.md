# Design

## Context

A disposição de dados no encerramento vive em um único lugar:
`apps/cases/closure.py` — `acknowledge_case_receipt` (ciência do NIR) e
`administratively_close_case` compartilham
`_clean_acknowledged_clinical_data` (zeragem dos 7 campos clínicos/LLM,
delete das rows `CaseDocument`/`CaseAttachment`) e a coleta de nomes de
arquivos + deleção física best-effort em `on_commit`. As rotas de serve de
documento/anexo de todos os papéis **não têm guard de status**: o 404 de
caso limpo é consequência da inexistência das rows. O detalhe do caso no
painel (`apps/dashboard/views.py::_case_detail_context`) não expõe
sumário/estrutura; quem os apresenta é o presenter médico
(`apps/doctor/presenters.py::build_case_detail_context`, com builders de
seção privados ~90 linhas puras). O worker de anexos
(`apps/attachments/tasks.py`) é o único passo do pipeline **sem**
verificação de status: sua proteção contra corrida com o encerramento era
a deleção da row ("DoesNotExist → no-op"). Projeto em piloto (produção com
pacientes reais desde 2026-09-13); specs `case-closure` e `dashboard`
atuais contratam a minimização incondicional e a ausência do sumário no
painel — este change as modifica (ver deltas).

## Goals / Non-Goals

**Goals:**

- Modo de retenção configurável por ambiente no encerramento (default:
  retenção durante a fase de estabilização), sem tocar FSM, rotas, guards
  de papel, notificações ou schema.
- Reversão operacional completa: flip do flag reativa a minimização;
  comando de purga minimiza retroativamente o acumulado.
- Supervisor (manager/admin) vê no painel o mesmo sumário clínico e a
  mesma estrutura extraída apresentados ao médico, em card collapsible —
  incluindo casos encerrados retidos.

**Non-Goals:**

- Não muda o conjunto de estados da FSM (contrato de 17 estados) nem
  cria transições novas.
- Não estende a visibilidade do sumário ao NIR/agendador (detalhe do
  agendador permanece limitado por decisão do dono 2026-09-08; NIR não
  re-identifica — D8 do change 05).
- Não introduz snapshot histórico congelado: os artefatos persistidos no
  caso SÃO os apresentados ao médico (a FSM nunca reentra no pipeline
  pós-decisão); o card lê o estado atual.
- Não cria política de retenção permanente nem TTL automático — a purga é
  operacional (comando), executada quando o dono decidir.

## Decisions

### D1 — Flag `CASE_CLOSURE_MINIMIZATION_ENABLED` (default `false`) + cabeamento em produção

Env-driven em `config/settings/base.py`, padrão dos flags existentes
(`INTAKE_ENABLED`, `*_RUN_TASKS_INLINE`), documentado em `.env.example`.
Semântica **positiva de minimização** (comportamento de longo prazo) com
default `false` = retenção, conforme decisão do dono para a fase de
estabilização — o flip futuro é ligar o default, não mudar de nome. Lido
no momento do serviço (nunca em import-time). **Cabeamento em produção**:
`docker-compose.prod.yml` injeta environment por lista explícita — o
serviço `web` ganha o passthrough
`CASE_CLOSURE_MINIMIZATION_ENABLED: ${CASE_CLOSURE_MINIMIZATION_ENABLED:-false}`
(mesmo padrão de `INTAKE_ENABLED` no arquivo), sem o qual o flag não
chega ao container e a reversão prometida não existe no ambiente com
pacientes reais. Override adicional em `config/settings/prod.py` fica
registrado como opção futura (se o dono quiser ativação explícita por
ambiente em vez de default herdado). Alternativa rejeitada: flag
`RETENTION_MODE` com três estados — não há terceiro comportamento a
governar.

### D2 — Forma do gate: função interna incondicional, decisão de modo nos entry points

**`_clean_acknowledged_clinical_data` permanece INCONDICIONAL**
(semântica atual de força total — é a mesma função que a purga retroativa
do slice 003 reusa; se ela lesse o flag, a purga nasceria morta sob o
default `false`). A decisão de modo mora **exclusivamente** nos dois
entry points (`acknowledge_case_receipt` e
`administratively_close_case`): com retenção ativa eles **pulam** a
chamada à limpeza E a coleta/deleção de arquivos físicos; com minimização
ativa o caminho atual é percorrido byte-a-byte. Transições FSM
(`nir_acknowledge`→`start_cleaning`→`complete_cleaning`,
`administratively_close`), eventos `CASE_STATUS_*`/auditoria, release de
lock e notificação do criador permanecem idênticos nos dois modos. O
estado transitório `CLEANING` ("Limpando dados") percorre o fluxo sem
efeito sobre dados em modo retenção — aceito porque o conjunto de
estados é contrato; comentário no código registra o porquê. Corolário:
`apps/intake/tests/test_header_persistence.py` (que chama a função
interna diretamente) segue verde SEM pino — ele testa a semântica
interna, não o modo. Alternativa rejeitada: gate dentro da função interna
— tornaria a purga no-op silencioso sob o default e quebraria o teste de
persistência sem pino. Alternativa rejeitada: transições condicionais
distintas por modo — duplicaria a FSM e o contrato de eventos para zero
ganho observável.

### D3 — Acesso a documentos/anexos retidos cai de graça

Sem guard de status nas rotas de serve (verificado), as rows retidas
restauram o acesso automaticamente sob os guards existentes (criador no
NIR; matriz de subtipo no médico; papel no painel/agendador). Nenhuma
rota muda; o cenário «Documento de caso encerrado é inacessível» passa a
ser condicional ao modo na spec.

### D4 — Purga retroativa: serviço em `closure.py` + comando fino

**Serviço puro** `purge_closed_case_data(...)` em `apps/cases/closure.py`
(mesmo módulo e MESMA semântica minimizante de
`_clean_acknowledged_clinical_data` — uma fonte, sem duplicação contra
`_CLEANED_EMPTY_VALUES`); **comando fino**
`manage.py purge_closed_case_data [--dry-run]` em `apps/cases` apenas
orchestra (seleção, laço por caso, saída). Seleciona apenas casos
`CLEANED` **com dados retidos** (≥1 campo clínico/LLM não-vazio ou rows
de documento/anexo); por caso, no mesmo padrão transacional do closure
(atomic por caso — falha no meio não deixa meia-purga), com evento
auditável **novo** `CASE_RETAINED_DATA_PURGED` (extensão aditiva do enum
fechado `CaseEventType`; verificado: todos os receivers de
`CaseEvent.post_save` filtram por `event_type` — nenhum dispara
comunicação/notificação/enqueue para o tipo novo). **Payload do evento e saída SEM
PHI**: apenas os NOMES dos 7 campos zerados (constantes) e CONTAGENS de
rows/arquivos — nunca conteúdo clínico nem nomes de arquivo (filename de
anexo pode conter nome de paciente). **Trilha em duas fases** (review 1
do slice 003): (1) evento `CASE_RETAINED_DATA_PURGED` DENTRO do atomic
durável, junto com a limpeza — payload = nomes dos campos + rows zeradas
+ `files_attempted`; prova a destruição do banco atomicamente com ela
(crash entre commit e evento é impossível); (2) após a deleção física,
evento COMPLEMENTAR de TIPO PRÓPRIO `CASE_RETAINED_DATA_PURGE_OUTCOME`
(somente quando `files_attempted > 0`)
com payload `files_deleted`/`files_failed` — o desfecho físico fica na
trilha; crash antes dele deixa a destruição provada com desfecho
desconhecido (aceito e documentado). Re-execução é no-op (sem eventos).
**Commit real obrigatório**: o atomic do serviço usa `durable=True`
(recusa transação externa com `RuntimeError` nomeado — a deleção física
só pode ocorrer após commit real, nunca sob savepoint cujo rollback
externo deixaria o banco apontando arquivos apagados); o comando roda em
autocommit; testes usam `TransactionTestCase` com commits reais.
O evento complementar usa TIPO PRÓPRIO `CASE_RETAINED_DATA_PURGE_OUTCOME`
(rótulo «Remoção dos arquivos da purga») com badge CONSCIENTE DO PAYLOAD:
`files_failed > 0` → badge de perigo (purga incompleta visível na trilha
do painel), senão badge neutro de sistema — a fase de purga e o desfecho
físico ficam DISTINTOS e legíveis na apresentação (review 2 do slice 003).
**Falha física de arquivo é reportada, não engolida**: contagens
`files_attempted/files_deleted/files_failed` por caso e no resumo; caso
com `files_failed > 0` é sinalizado como purga incompleta (o banco
commitou, mas o órfão com PHI precisa de tratamento — a reexecução não
redescobre o caso pelo critério de seleção, então a falha TEM que
aparecer na saída, no evento complementar e em log) e o comando encerra
com exit code não-zero listando os casos afetados. **Log sem PHI**: em
falha de arquivo, registra referência do caso + contagem + TIPO da
exceção — sem caminho, sem mensagem da exceção e sem `exc_info` (o
traceback carrega o filename). Idempotência natural: caso sem dados retidos
é pulado, sem evento. `--dry-run` lista referência + contagens sem tocar
nada. **Ordem operacional da reversão** (runbook registrado no ADR-0010):
1) `CASE_CLOSURE_MINIMIZATION_ENABLED=true` no ambiente (nova disposição
para encerramentos) → 2) `--dry-run` → 3) purga real → 4) registro da
execução (data, escopo, contagens, responsável) no runbook do ADR.
Alternativa rejeitada: reusar um evento existente — nenhum casa, e a
purga destrói dado retido: a trilha é o único registro restante.
Alternativa rejeitada: lógica de purga dentro do comando — drift futuro
contra a semântica do closure.

### D5 — Builders de seção compartilhados; presenter próprio no dashboard

Os builders puros de `apps/doctor/presenters.py` migram para
`apps/cases/presentation.py` (funções puras sobre estrutura JÁ
re-identificada — sem dependência de `apps.anonymization`, sem ciclo;
`apps/cases` é o núcleo que ambos os presenters já importam). **Escopo
completo da extração**: os 8 builders de linha, as
chaves/títulos (`_STRUCTURE_SECTION_KEYS`/`_STRUCTURE_SECTION_TITLES`),
os helpers `_as_str`/`_as_list` E os mapas de que dependem
(`_PROFILE_BY_TYPE` e `_EXAM_LABELS` — derivados em `presentation.py` a
partir de `PROCEDURE_PROFILES`, sem import do app médico), expondo **uma
API pública única** `build_structure_sections(structured) -> list` que
os DOIS presenters consomem — o loop de montagem não é reimplementado no
painel, senão a paridade volta a depender de disciplina. O presenter
médico passa a importar de lá (refactor sem mudança de comportamento).
Novo `apps/dashboard/presenters.py::build_case_summary_context(case)`:
`summary_lines` (linhas não-vazias de `reidentify_text`) + `sections`
(API compartilhada sobre `reidentify_structure`) + flag
`has_structure`, consumido por `_case_detail_context`. Alternativas
rejeitadas: (a) dashboard importar `apps.doctor.presenters` —
dependência cruzada app-papel→app-papel + queries de prior-case/anexos
desnecessárias; (b) duplicar builders — risco de drift de paridade entre
o que o médico viu e o que o auditor vê.

### D6 — Card no painel: um card, dois blocos, fechado por padrão

Card «Sumário apresentado ao médico» (título decidido aqui) posicionado
após «Procedimentos declarados» e antes de «Texto Extraído do PDF»
(espelha a ordem de leitura clínica do médico: quadro clínico antes dos
documentos). Collapsible Bootstrap (`data-bs-toggle="collapse"`,
fechado por padrão, mesmo molde da trilha/texto). Dentro: bloco «Sumário
clínico» + bloco «Estrutura extraída» com o MESMO markup dos cards
médicos (`<p>` por linha; `<h3>` por seção + `<ul>`) — a fonte é a mesma
(D5), garantindo paridade. Sem artefato, o card permanece com
placeholders explícitos («Sumário clínico não disponível para este
caso.» / «Nenhuma estrutura extraída.»), espelhando o contrato do médico
— para o auditor, a ausência visível é informação. Os placeholders são
**independentes por bloco**: um caso que falhou após o LLM1 (estrutura
sem sumário) mostra a estrutura e placeholder apenas no sumário —
paridade estrita com o template médico.

### D7 — Testes nos dois modos, pinados por teste

Suítes existentes que assertam a minimização
(`test_closure_ack.py`, `test_administrative_closure.py`,
`test_closure_worker_abort.py`, serve 404 de caso limpo) ganham
`override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True)` explícito;
novos testes de retenção pinam `False`. Nenhum teste depende do default
do ambiente — os dois modos ficam sempre cobertos. Exceção deliberada:
`test_header_persistence.py` chama a função interna diretamente e segue
sem pino (D2). Novo arquivo de teste do card segue o molde
`apps/dashboard/tests/test_detail_cards.py` (HTTP, login manager,
asserções de HTML); o worker-abort pós-encerramento permanece válido nos
dois modos (escrita abortada por status `CLEANED` — e o worker de anexos
passa a ter o MESMO gate explícito, D8).

### D8 — Worker de anexos: gates serializados por transações curtas + evento de dispatch durável

O worker de anexos (`apps/attachments/tasks.py`) é o único passo sem
verificação de status: sua única proteção contra corrida com o
encerramento era a **deleção da row** ("DoesNotExist → no-op") — proteção
que a retenção remove (a row sobrevive). Correção em três camadas
(iterada pelas reviews 1 e 2 do slice 001):

- **(a) Gates rápidos** de status entre etapas (re-leitura barata, sem
  lock — fast path);
- **(b) Transações CURTAS com `select_for_update` no `Case`** em cada
  ponto de decisão/escrita: status re-lido sob o lock DENTRO da mesma
  transação da escrita/evento (persistência de extração, anonimização,
  verificação em sucesso E falha, `_fail_attachment`). **Nenhuma
  transação longa segura o lock durante chamadas externas** — não existe
  branch de compensação; a trilha segue append-only;
- **(c) Dispatch durável e paginado**: imediatamente antes do envio externo
  (APÓS a rasterização), transação curta re-lê o status sob o lock e insere
  o `CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED`, com COMMIT ANTES do envio —
  o registro de auditoria sobrevive a falha/crash/processo morto após o
  PHI sair (spec `attachments`: evento anterior ao envio, só quando o
  envio é possível; paridade com o comportamento pré-change, que já
  inseria em autocommit). O OCR multipágina envia UMA página por chamada:
  o guard de status (transação curta, sem lock retido) roda ANTES DE CADA
  página — encerramento commitado entre páginas aborta as restantes
  (exceção tipada → no-op da etapa; o `DISPATCHED` já commitado PERMANECE,
  porque as páginas já enviadas saíram com o caso aberto).

**Janela residual deliberada e aceita**: entre o commit do evento de
_dispatch_ e o início de CADA envio HTTP de página — curta (nenhuma I/O de
rede entre eles; inclui apenas a inicialização do cliente e a codificação
  das imagens). A prova automatizada de durabilidade usa transação real
  (`TransactionTestCase`) e conexão de banco INDEPENDENTE (psycopg direto)
  para consultar o evento após a interrupção.
Alternativas estritas rejeitadas com registro: (1) segurar o lock do row
 durante o envio fecharia a janela, mas o rollback da transação apagaria
  o evento de auditoria quando o processo falhasse após o PHI ter saído
  (P1 do review 2 — pior que a janela); (2) lease operacional exclusivo
  (`claim_case_lock`) serializaria com o encerramento administrativo,
  mas CONFLITARIA com o lock do pipeline LLM que roda EM PARALELO sobre o
  mesmo caso por design (change 10) e empurraria os anexos para trás do
  pipeline.

Escritas pós-envio/pós-LLM são gateadas sob lock na própria transação de
persistência (`CLEANED` → no-op, sem evento, sem fail). Para compor
request→persist com gates entre eles, `verification.py` expõe UMA operação
pública de request/normalização/classificação —
`request_verification_outcome(attachment)` (token → request → decode →
força `UNKNOWN` sem token; falhas tipadas propagam) + tupla pública
`VERIFICATION_FAILURE_EXCEPTIONS` + persistências — consumida por
`tasks.py` (com gates) e pelo próprio `verify_attachment` (referência sem
gate, coberta por testes próprios): nenhuma lista de exceções ou regra de
normalização duplicada entre worker e referência (desvios de boundary
autorizados e registrados no slice doc). Testes provam as
DUAS ordens (encerramento-primeiro → no-op, com spy provando não-envio e
não-persistência; worker-primeiro → encerramento aplica a disposição do
modo), nos dois modos e nos dois caminhos de encerramento (ciência e
administrativo), incluindo sucesso E falha da verificação — sem
manipulação da trilha. Teste de durabilidade: exceção no envio após o
commit do evento → evento permanece (consulta fresca).

### D9 — Textos de UI e rótulo de trilha verdadeiros nos dois modos

Cinco superfícies hoje AFIRMAM a minimização no ato em que deveriam
informar: `templates/intake/case_detail.html` ("os dados clínicos
sensíveis são removidos", exibido ao NIR antes de confirmar a ciência),
`templates/dashboard/admin_close_confirm.html` e
`templates/dashboard/case_detail.html` ("os dados clínicos são
minimizados"), `apps/dashboard/event_labels.py`
(`CASE_STATUS_CLEANED: "Caso concluído e dados minimizados"` — ficaria
falso na própria trilha de auditoria) e `templates/accounts/manual.html`.
Com retenção default, todas essas frases mentem. Correção: textos
CONDICIONAIS ao modo (flag no contexto) ou neutros ("disposição dos
dados conforme a política vigente — ADR-0010"), e rótulo de
`CASE_STATUS_CLEANED` renomeado para verdadeiro nos dois modos ("Caso
concluído"). Testes que pinam as strings existentes são atualizados no
slice 001.

## Risks / Trade-offs

- [Retenção acumula PHI real + `pseudonym_map` no piloto em produção] →
  ADR-0010 registra fundamento, escopo e **gatilho de reversão**; a
  reversão é operacional (flag por ambiente + purga `--dry-run` antes da
  execução real); sem reversão, a minimização original permanece no
  código e nas specs como modo suportado.
- [Fundamento jurídico e gatilho vagos] → o ADR-0010 exige validação
  jurídica explícita da base legal **por finalidade** (auditoria da
  assistência ≠ benchmark de produto — finalidades secundárias distintas)
  e gatilho de reversão verificável com responsável nomeado; pendência do
  dono que bloqueia o archive do slice 001, não o código (ver Open
  Questions).
- [Purga destrói material de benchmark/auditoria definitivamente] → é a
  semântica da minimização original; `--dry-run`, evento auditável por
  caso, contagens de falha física reportadas (D4) e execução registrada
  em runbook tornam o ato deliberado e rastreável.
- [Órfão de arquivo físico após falha na purga é irredescobrível] →
  contagens `files_failed` por caso + exit code não-zero + registro no
  runbook (D4); a falha fica visível no ato, não silenciosa.
- [Worker de anexos processa caso já encerrado na fase de retenção] →
  gate explícito de status com teste de corrida (D8).
- [Textos de UI/trilha afirmam minimização que não ocorrerá] → textos
  condicionais/neutros + rótulo renomeado (D9).
- [Custo de storage cresce com arquivos retidos] → aceito na fase de
  estabilização; volume do piloto é pequeno; monitorável por contagem no
  `--dry-run`.
- [Drift de paridade médico×painel no sumário] → fonte única dos
  builders + API pública única consumida pelos dois presenters (D5);
  teste de paridade compara os dois contextos.
- [Suítes legadas assumem minimização incondicional] → pinos explícitos
  por teste (D7); gate pequeno e de forma fixa (D2).
- [`CLEANING` transitório sem efeito em modo retenção] → aceito
  (contrato de estados); documentado no código.

## Migration Plan

1. Deploy é só código (sem migration de schema — o novo valor de
   `CaseEventType` é aditivo em `TextChoices`; passthrough de env no
   compose).
2. Comportamento muda no deploy: encerramentos passam a reter
   (default). Casos já `CLEANED` antes do deploy permanecem minimizados
   (a purga nunca precisa tocá-los — não têm dados).
3. Rollback: `CASE_CLOSURE_MINIMIZATION_ENABLED=true` no ambiente do
   serviço `web` restaura a minimização imediatamente (cabeamento do
   D1); a purga retroativa limpa o acumulado da fase de retenção quando
   o dono decidir, seguindo o runbook do D4.

## Open Questions

(nenhuma — as pendências do ADR foram resolvidas pelo dono em 2026-11-17 e
serão registradas no ADR-0010: **base jurídica** = execução íntegra e
exclusiva dentro do perímetro do hospital com autorização expressa dos
gestores, cobrindo as duas finalidades — auditoria supervisora da
assistência e benchmark de produto com corpus real; **gatilho de reversão**
= o produto alcançar a **versão 1.0**; **responsável** pela reversão e
pelo procedimento de acesso ao corpus retido = **gerência de TI do
hospital**.)
