# doctor-decision Specification

## Purpose

Apresentar ao médico, com dados reais do paciente e sob controle de acesso por
subtipo, os casos prontos para decisão — com os alertas e a recomendação
consultiva da policy — e registrar a decisão por procedimento com trilha
auditável, fechando o estágio entre a sumarização e o agendamento.

## Requirements

### Requirement: Fila médica operacional com filtro por estado e subtipo

O sistema SHALL exibir ao papel ativo `doctor`/`admin` as abas `Pendentes` (`AWAITING_DOCTOR`) e `Decididos` (estados pós-decisão), ordenadas por `days_on_screen` desc nulls-last e FIFO, com filtro server-side único por subtipo e busca client-side por nome/ocorrência. Em desktop, cada card SHALL separar conteúdo (8/12) e tempo/ação (4/12). Pendentes exibem espera uma única vez e ações de reserva; decididos exibem disposição e data/hora por procedimento e «Ver detalhes», sem espera. Guards, paginação, partial a cada 20 s e contadores são preservados; uid nunca é exibido.

#### Scenario: Médico de subtipo filtra a fila

- **GIVEN** um médico com subtipo `angio` e casos em `AWAITING_DOCTOR` de subtipos `angio` e `cardio`
- **WHEN** acessa a fila com o filtro `angio`
- **THEN** apenas casos com ao menos um tipo declarado de subtipo `angio` são listados

#### Scenario: Generalista vê qualquer subtipo

- **GIVEN** um médico sem subtipos atribuídos e casos em `AWAITING_DOCTOR` de subtipos distintos
- **WHEN** acessa a fila com o filtro `Todas`
- **THEN** casos de todos os subtipos são listados

#### Scenario: Papel não médico recebe 403

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** acessa a fila médica
- **THEN** recebe HTTP 403 sem listagem

#### Scenario: Fila ordenada pelo tempo de tela

- **GIVEN** casos em `AWAITING_DOCTOR` com `days_on_screen` 10, 3 e um sem cabeçalho (upload mais antigo)
- **WHEN** o médico acessa a fila
- **THEN** a ordem é 10, 3 e o caso sem cabeçalho ao fim (desempate FIFO), independentemente do momento do upload

#### Scenario: Card pendente com identificação e tempo de espera únicos

- **GIVEN** um caso em `AWAITING_DOCTOR` com nome, idade 84, sexo, origem e `days_on_screen` 6
- **WHEN** o médico acessa a aba aguardando
- **THEN** o card exibe nome (`84 a`), ocorrência e demografia disponível, o badge «Dias em tela: 6» e exatamente um rótulo «Aguardando há …», sem repetir a data de recebimento

#### Scenario: Card decidido orientado ao resultado

- **GIVEN** um caso decidido com dois procedimentos, disposições e horários persistidos
- **WHEN** o médico acessa a aba decididos
- **THEN** o card exibe nome/idade, ocorrência, disposição e data/hora de cada procedimento e o botão «Ver detalhes», sem «Aguardando há», «Recebido há» ou `waiting-time`

#### Scenario: Decisões mistas não são agregadas visualmente

- **GIVEN** um caso decidido com um procedimento aprovado e outro negado
- **WHEN** o card da aba decididos é renderizado
- **THEN** cada procedimento mantém seu próprio badge de disposição, aprovado em verde e negado em vermelho, sem um resultado único para o caso

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a fila é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Caso reservado por outro médico aparece bloqueado

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva ativa de outro médico
- **WHEN** o médico acessa a aba aguardando
- **THEN** o card exibe «Reservado» desabilitado com o nome do portador, sem ação primária ou link de abertura

#### Scenario: Reserva do próprio médico oferece continuar

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva ativa do próprio médico
- **WHEN** ele acessa a aba aguardando
- **THEN** o card exibe a ação «Continuar avaliação» que reabre a página de trabalho

#### Scenario: Reserva vencida não bloqueia o card

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva cuja lease venceu
- **WHEN** o médico acessa a fila
- **THEN** a reserva vencida é expirada antes da montagem e o card exibe a ação «Avaliar» normalmente

#### Scenario: Abas usam Pendentes e Decididos

- **GIVEN** casos aguardando e em estados pós-decisão (`DOCTOR_DENIED`, `DOCTOR_ACCEPTED`, `SCHEDULER_REQUESTED`)
- **WHEN** o médico abre a fila
- **THEN** as abas exibem `Pendentes` e `Decididos`, e decididos lista esses estados independentemente da data da decisão

#### Scenario: Título e container acompanham a aba

- **GIVEN** a fila médica aberta em qualquer aba
- **WHEN** a página é renderizada em desktop
- **THEN** o título é «Casos aguardando decisão» em pendentes ou «Casos decididos» em decididos, e título, filtros, alertas e cards ocupam a mesma largura do container do header, sem margem superior extra

#### Scenario: Card operacional tem duas regiões no desktop

- **GIVEN** ao menos um card na fila em viewport desktop
- **WHEN** o card é renderizado
- **THEN** identificação e resumo ocupam a região principal de 8/12 e tempo/resultado/ação ocupam 4/12 à direita, dentro de uma única superfície de card, sem item de lista aninhado

#### Scenario: Filtro de subtipo tem um único controle

- **GIVEN** a fila com subtipos disponíveis e contagens de pendentes
- **WHEN** a aba aguardando é renderizada
- **THEN** existe apenas um seletor de subtipo, submetido ao server, com as contagens de pendentes em suas opções; nenhum segundo seletor client-side de exame/subtipo é exibido

#### Scenario: Decididos não exibe contadores de pendentes no filtro

- **GIVEN** a aba decididos aberta
- **WHEN** o filtro de subtipo é exibido
- **THEN** suas opções não anunciam contagens de pendentes e nenhuma linha «Pendentes por subtipo» aparece

#### Scenario: Partial atualiza a fila sem reload

- **GIVEN** a fila aberta e um novo caso chegando a `AWAITING_DOCTOR`, compatível com o subtipo ativo e posicionado na página exibida
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba, filtro, busca e paginação

#### Scenario: Caso fora do filtro ou da página não aparece

- **GIVEN** a fila filtrada por um subtipo e um novo caso de outro subtipo, ou um caso posicionado em outra página
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece na página exibida

#### Scenario: Busca client-side filtra os cards

- **GIVEN** a fila com vários casos renderizados
- **WHEN** o médico digita o nome do paciente ou o nº de ocorrência na busca
- **THEN** apenas os cards correspondentes permanecem visíveis, sem nova requisição e sem alterar o escopo do filtro server-side

#### Scenario: Badges de contagem nas abas

- **GIVEN** 3 casos aguardando e 5 decididos acessíveis ao médico
- **WHEN** a fila é renderizada
- **THEN** as abas exibem as contagens 3 e 5

#### Scenario: Estado vazio é informativo

- **GIVEN** uma aba sem casos no escopo atual
- **WHEN** ela é renderizada
- **THEN** um alerta informativo informa que não há casos aguardando avaliação ou que não há casos decididos, sem um segundo card vazio redundante

#### Scenario: Layout permanece utilizável no mobile

- **GIVEN** um card com nome longo, múltiplos procedimentos e ação
- **WHEN** a fila é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, a ação permanece acessível com alvo mínimo de 44 px e não há overflow horizontal

### Requirement: Access control por subtipo no conjunto declarado

O sistema SHALL restringir médico com subtipos atribuídos a decidir apenas
casos cujo conjunto de procedimentos declarados inclua ao menos um tipo do seu
subtipo; generalista (sem subtipo) e `admin` acessam qualquer caso. A checagem
SHALL ser aplicada na abertura do detalhe e reforcada no envio da decisão —
nunca apenas no template.

#### Scenario: Médico de subtipo abre caso fora do seu subtipo

- **GIVEN** médico com subtipo `cardio` e um caso declarado apenas com tipos de subtipo `angio`
- **WHEN** tenta abrir o detalhe de decisão
- **THEN** recebe HTTP 403 sem exposição de dados do paciente

#### Scenario: Tentativa de decisão fora do subtipo é rejeitada

- **GIVEN** o mesmo médico e caso
- **WHEN** envia diretamente o POST de decisão do caso
- **THEN** a submissão é rejeitada com HTTP 403 e nenhuma decisão é persistida

### Requirement: Presenter re-identificado sob papel autorizado

O detalhe do caso SHALL apresentar, apenas para `doctor`/`admin`, os dados reais do paciente (identificação, número de ocorrência) e a demografia do caso — idade, sexo e raça/cor extraídas do cabeçalho padrão SESAB — e os artefatos do pipeline re-identificados: histórico e sumário, estrutura extraída, alertas da policy com a recomendação por procedimento e o resultado agregado, requisitos gerais acionáveis e o PDF original. A re-identificação SHALL ocorrer apenas na renderização para papel autorizado; nenhum artefato re-identificado é persistido nem enviado a qualquer LLM. A ordem dos cards SHALL seguir a leitura clínica: identificação, «Procedimentos do caso», sumário clínico e estrutura extraída (o quadro clínico) ANTES dos alertas consultivos e demais cards da automação. O detalhe exibe a trilha de eventos em card collapsible «Trilha de eventos» ao final da página — a mesma trilha do painel (paridade total de auditoria): cada evento com rótulo legível, badge por categoria, ator com papel em português e data/hora, card fechado por padrão. O card «Procedimentos do caso» exibe TODAS as rows — inclusive a detectada não-declarada que sobrevive ao bypass da divergência — com badges de origem (Declarado/Detectado na extração) e de detecção após a reconciliação. O `<title>`, o breadcrumb e os links de navegação entre detalhe e decisão SHALL usar a **referência humana** do caso — nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel) — e o uid completo NÃO aparece em texto visível nessas superfícies (permanece apenas como chave das URLs).

#### Scenario: Médico vê o sumário re-identificado

- **GIVEN** um caso em `AWAITING_DOCTOR` cujo `summary_text` contém o token `<PESSOA_1>`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o sumário é renderizado com o nome real do paciente no lugar do token

#### Scenario: Requisitos gerais acionáveis exibidos conforme alertas

- **GIVEN** um caso cujo policy gerou alerta de anticoagulante com protocolo de suspensão por fármaco
- **WHEN** o médico abre o detalhe
- **THEN** o card do procedimento exibe o requisito geral com o protocolo acionável correspondente ao alerta

#### Scenario: Recomendação consultiva visível por procedimento

- **GIVEN** um caso com `policy_result` recomendando recusar um procedimento com motivos e a sugestão agregada de recusa
- **WHEN** o médico abre o detalhe
- **THEN** os motivos da recusa por procedimento e o agregado são exibidos como alerta consultivo, sem bloquear a decisão

#### Scenario: Demografia do paciente no card de identificação

- **GIVEN** um caso com `patient_age`=84, `patient_gender`=`F` e `patient_race`=`Parda` extraídos do cabeçalho SESAB
- **WHEN** o médico autorizado abre o detalhe
- **THEN** o card de identificação exibe idade (84 a), sexo (F) e raça/cor (Parda) ao lado de nome e nº de ocorrência

#### Scenario: Demografia ausente mostra o placeholder padrão

- **GIVEN** um caso sem cabeçalho SESAB (campos de demografia vazios)
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as linhas de idade, sexo e raça/cor exibem «—» como os demais campos ausentes do card, sem erro

#### Scenario: Quadro clínico antes do consultivo

- **GIVEN** um caso em `AWAITING_DOCTOR` com procedimentos declarados, sumário clínico, estrutura extraída e alertas consultivos
- **WHEN** o médico abre o detalhe
- **THEN** os títulos aparecem na ordem: Procedimentos do caso → Sumário clínico → Estrutura extraída → Alertas consultivos (o quadro clínico precede o consultivo da automação)

#### Scenario: Detalhe em decisão sem trilha de eventos

- **GIVEN** um caso em `AWAITING_DOCTOR` com trilha de eventos
- **WHEN** o médico abre o detalhe
- **THEN** o card «Trilha de eventos» é renderizado ao final da página, fechado por padrão; ao abrir, cada evento exibe rótulo legível, ator com papel em português e data/hora

#### Scenario: Trilha do médico mostra criação e pós-decisão

- **GIVEN** um caso em `AWAITING_DOCTOR` criado por um NIR, com pipeline concluído
- **WHEN** o médico autorizado abre o detalhe e expande a trilha de eventos
- **THEN** os eventos exibem o NIR como ator da criação, os eventos do pipeline com ator «Sistema» e as datas/horas de cada etapa

#### Scenario: Detalhe médico exibe row detectada não-declarada

- **GIVEN** um caso em `AWAITING_DOCTOR` cuja divergência foi liberada por bypass (declarada `angio_art_perif` não-detectada + `art_perif` detectada não-declarada persistida)
- **WHEN** o médico com especialidade compatível abre o detalhe
- **THEN** o card «Procedimentos do caso» exibe ambas as rows com rótulos legíveis e os badges «Não detectado» e «Detectado na extração»

#### Scenario: Detalhe e decisão usam a referência humana

- **GIVEN** um caso em `AWAITING_DOCTOR` com nº de ocorrência
- **WHEN** o médico abre o detalhe e a página de decisão
- **THEN** `<title>`, breadcrumbs e o link do breadcrumb «Caso …» exibem o nº de ocorrência (`uid <8 chars>` quando o nº está ausente) e o uid completo não aparece em texto visível — apenas nos hrefs

### Requirement: Card de prior-case com motivo real

O detalhe do caso SHALL exibir, por procedimento declarado com caso prévio
encontrado (número de ocorrência ou fallback nome+nascimento), um card com
data, desfecho e motivo real da decisão prévia. O badge do caso prévio SHALL
exibir a **referência desambiguada** do prévio — `nº · <8 primeiros chars do
uid>` (ou `uid <8 chars>` quando sem nº) — e não o uid completo. O lookup do
presenter é somente-leitura (eventos de lookup permanecem os do pipeline) e
respeita as mesmas janelas configuráveis do pipeline.

#### Scenario: Card de negativa prévia por número de ocorrência

- **GIVEN** um caso cujo procedimento tem caso prévio negado há 3 dias com o mesmo número de ocorrência
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe data, desfecho negado e o motivo real registrado pelo médico prévio

#### Scenario: PDF original restrito a papel autorizado

- **GIVEN** um caso com documento PDF
- **WHEN** um usuário com papel ativo `nir` tenta acessar a URL do PDF médico diretamente
- **THEN** recebe HTTP 403 sem conteúdo do arquivo

#### Scenario: Card de prior-case por fallback nome+nascimento

- **GIVEN** um caso cujo procedimento tem caso prévio negado há 10 dias, número de ocorrência diferente, mesmo nome normalizado e nascimento
- **WHEN** o médico abre o detalhe
- **THEN** o card exibe o prévio com desfecho e motivo real, marcando a origem do match

#### Scenario: Badge do prior-case usa referência desambiguada

- **GIVEN** um caso cujo procedimento tem caso prévio com nº de ocorrência N (mesmo nº do caso corrente, originado de reenvio corrigido)
- **WHEN** o médico abre o detalhe
- **THEN** o badge do prior-case exibe `N · <8 primeiros chars do uid do prévio>` — o uid completo do prévio não aparece em texto visível

### Requirement: Decisão por procedimento com motivo obrigatório em negativas

O sistema SHALL registrar a decisão médica por procedimento declarado
(`approved`/`denied`) com motivo obrigatório quando negado, validando no
formulário e persistindo rows, evento auditável e transição FSM no mesmo
atomic (todos negados → `DOCTOR_DENIED`; ao menos um aprovado →
`DOCTOR_ACCEPTED` com encadeamento para a fila de agendamento). A avaliação
acontece numa **página de trabalho única**: a leitura completa do caso
(identificação, procedimentos, sumário, estrutura, prior-case, documentos,
anexos, requisitos, recomendação agregada e alertas consultivos) precede o
formulário de decisão como último bloco da mesma página, com âncora
«Ir para o formulário de decisão» no topo e as ações [Registrar decisão] e
[Sair sem decidir] juntas ao fim do formulário. A submissão SHALL exigir
reserva vigente do caso (token de lock emitido na abertura) e, ao concluir
com sucesso, liberar a reserva. Submissão concorrente ou fora de
`AWAITING_DOCTOR` SHALL falhar sem efeito parcial, com mensagem clara ao
usuário.

#### Scenario: Negação exige motivo

- **GIVEN** o formulário com um procedimento marcado `denied` e motivo em branco
- **WHEN** o médico envia
- **THEN** o formulário rejeita com erro de validação e nada é persistido

#### Scenario: Decisão mista leva o caso ao agendamento

- **GIVEN** um caso com dois procedimentos declarados em `AWAITING_DOCTOR`
- **WHEN** o médico aprova um e nega o outro com motivo
- **THEN** as rows registram as disposições, o evento resume as decisões e o caso transita para a fila de agendamento

#### Scenario: Dupla submissão falha sem efeito parcial

- **GIVEN** um caso que acabou de ser decidido por outro médico
- **WHEN** uma segunda submissão chega para o mesmo caso
- **THEN** ela falha com mensagem de estado e nenhuma decisão adicional é gravada

#### Scenario: Avaliação e decisão num fluxo único

- **GIVEN** um caso em `AWAITING_DOCTOR`
- **WHEN** o médico abre a página de trabalho a partir da fila
- **THEN** a página apresenta a leitura completa e, ao fim, o formulário de decisão; a âncora «Ir para o formulário de decisão» existe no topo e o bloco «Decisões registradas» (histórico) não é renderizado nesta página

#### Scenario: Submissão sem reserva vigente falha

- **GIVEN** um caso em `AWAITING_DOCTOR` cujo token de reserva está ausente, inválido ou com lease vencida
- **WHEN** o médico envia o formulário de decisão
- **THEN** o formulário é reexibido com mensagem de reserva expirada e nada é persistido

#### Scenario: Decidir libera a reserva do caso

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico que decide
- **WHEN** a decisão é registrada com sucesso
- **THEN** a reserva é liberada e o caso segue o fluxo pós-decisão imediatamente disponível às filas seguintes

### Requirement: Caso decidido consultável

O sistema SHALL exibir, para `doctor`/`admin` com acesso ao caso (regra de subtipo), o detalhe read-only de casos decididos com as decisões por procedimento, motivos, ator e data — e a trilha de eventos em card collapsible «Trilha de eventos» ao final (a mesma do painel, fechado por padrão) — e sem permitir nova decisão fora de `AWAITING_DOCTOR`.

#### Scenario: Detalhe decidido read-only

- **GIVEN** um caso em `DOCTOR_DENIED`
- **WHEN** o médico autorizado abre o detalhe
- **THEN** as decisões e motivos são exibidos, nenhum formulário de decisão é renderizado e o card «Trilha de eventos» aparece ao final, fechado por padrão

### Requirement: Reserva de caso durante a avaliação médica

O sistema SHALL reservar o caso para o médico no momento em que ele abre a
página de trabalho de decisão (`AWAITING_DOCTOR`): claim atômico com
contexto próprio e lease longa (renovável), gravando evento auditável na
trilha. Reserva ativa de outro portador SHALL barrar a abertura com mensagem
identificando o portador e retorno à fila — nunca erro 500. A reserva SHALL
ser renovada automaticamente enquanto houver atividade na página (heartbeat
com grace de inatividade) e SHALL ser liberada imediatamente quando o médico
sai sem decidir ou quando a decisão é registrada; sem renovação, a reserva
SHALL expirar sozinha e devolver o caso à fila. O mesmo médico reabrindo o
caso renova a reserva com novo token.

#### Scenario: Abrir a avaliação reserva o caso

- **GIVEN** um caso em `AWAITING_DOCTOR` sem reserva
- **WHEN** o médico abre a página de trabalho
- **THEN** o caso fica reservado para ele (portador registrado, lease no futuro, evento de claim na trilha) e o formulário carrega o token da reserva

#### Scenario: Outro médico é barrado pela reserva ativa

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico A
- **WHEN** o médico B tenta abrir a página de trabalho
- **THEN** ele recebe mensagem de que o caso está reservado (com o portador) e é redirecionado à fila, sem alterar a reserva

#### Scenario: Sair sem decidir libera imediatamente

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico
- **WHEN** ele aciona «Sair sem decidir»
- **THEN** a reserva é liberada na hora (evento de release na trilha) e ele volta à fila com o caso disponível

#### Scenario: Inatividade expira a reserva

- **GIVEN** um caso em `AWAITING_DOCTOR` com reserva cuja lease venceu sem renovação
- **WHEN** qualquer médico acessa a fila
- **THEN** a reserva vencida é expirada e o caso volta a exibir a ação livre

#### Scenario: Reabrir o próprio caso renova a reserva

- **GIVEN** um caso em `AWAITING_DOCTOR` reservado pelo médico A
- **WHEN** o médico A reabre a página de trabalho
- **THEN** a reserva é renovada com novo token e nova lease, sem barrar o acesso

### Requirement: Aviso médico da confirmação NIR e da divergência automática

O detalhe e a página de decisão SHALL apresentar aviso consultivo da confirmação aplicada: procedimento confirmado, seleção mantida ou trocada, declaração anterior, detecção automática original e nova se diferente, autor, data e justificativa. O aviso SHALL distinguir confirmação operacional de evidência/aprovação clínica e preservar a decisão médica. Sem confirmação aplicada e correlacionada, não SHALL inventar autoria ou consentimento.

#### Scenario: Médico vê a contradição mesmo com seleção mantida
- **GIVEN** A declarado, B detectado e A ratificado pelo NIR, com reprocessamento concluído
- **WHEN** médico autorizado abre detalhe ou decisão
- **THEN** vê resumo e análise de A e aviso de seleção mantida, detecção B, NIR/data/justificativa e que a confirmação não é aprovação clínica

#### Scenario: Troca informa origem e destino sem inventar consenso
- **GIVEN** A declarado inicialmente e C confirmado após detecção B
- **WHEN** o médico abre a avaliação processada
- **THEN** o aviso distingue A, B e C, e a sugestão e formulário de decisão dizem respeito apenas a C; rows automáticas extras não viram decisões adicionais

#### Scenario: Aviso distingue detecção original e nova detecção
- **GIVEN** A declarado originalmente, B detectado na revisão, C confirmado pelo NIR e D detectado na nova extração
- **WHEN** médico autorizado abre detalhe ou decisão
- **THEN** o aviso identifica B como detecção original e D como resultado da nova extração, mantendo C como objeto da análise, sem apresentar a união de conflitos como se fosse a nova detecção

#### Scenario: Nenhum exame detectado é informado explicitamente
- **GIVEN** confirmação válida de A e reextração sem procedimento detectado
- **WHEN** o aviso é renderizado
- **THEN** informa ausência de detecção automática e confirmação de A, sem afirmar concordância do LLM

#### Scenario: Caso automático ou histórico não ganha aviso humano fictício
- **GIVEN** caso sem aplicação de confirmação humana correlacionável
- **WHEN** seu detalhe é renderizado
- **THEN** não há aviso afirmando confirmação pelo NIR, mesmo que haja declaração ou bypass antigo

#### Scenario: Justificativa é escapada e não persistida re-identificada
- **GIVEN** justificativa com markup e artefatos clínicos com tokens
- **WHEN** a página autorizada é renderizada
- **THEN** o texto livre é escapado, a re-identificação clínica ocorre só na renderização e nenhum payload re-identificado é enviado ao LLM

### Requirement: Avaliação médica corresponde ao procedimento confirmado e analisado

Nos novos casos revisados, acesso por subtipo, análise e decisão SHALL usar o procedimento confirmado como declaração corrente. Trabalho médico SHALL exigir processamento concluído com resumo não vazio e análise completa desse procedimento, também no POST direto. Falha de completude não SHALL ser tratada como aprovação clínica nem permitir decisão sobre relatório puro.

#### Scenario: Troca de subtipo atualiza alcance médico
- **GIVEN** troca de `cat_cardiaco` para `nefrostomia` confirmada e processada
- **WHEN** médicos exclusivos dos subtipos antigo e novo consultam fila, detalhe e ação de decisão
- **THEN** o alcance corresponde ao subtipo de `nefrostomia`, generalista/admin mantêm acesso e o subtipo antigo não obtém acesso por causa da declaração histórica

#### Scenario: Caso em revisão ou FAILED não entra na fila de decisão
- **GIVEN** caso novo ainda retido, reprocessando ou em falha
- **WHEN** o médico consulta pendentes ou tenta decidir diretamente
- **THEN** não é oferecido como pronto para decisão e o POST não grava decisão

#### Scenario: Inconsistência de artefatos barra trabalho médico
- **GIVEN** caso novo em estado de espera médica, mas com resumo ausente ou análise não correspondente ao confirmado
- **WHEN** é aberta a página de trabalho ou enviado POST direto
- **THEN** a decisão é recusada sem efeitos com informação clara de processamento inconsistente, em vez de exibir formulário decidível sobre relatório puro

#### Scenario: Aviso não bloqueia decisão clínica divergente
- **GIVEN** caso confirmado, integralmente processado, com aviso e recomendação de recusa
- **WHEN** médico autorizado decide aprovar com reserva válida
- **THEN** a decisão médica é aceita conforme as regras existentes, sem veto pela confirmação NIR ou recomendação consultiva
