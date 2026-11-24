# scheduling Specification

## Purpose

Confirmar, negar e reabrir o agendamento de casos aceitos pelo médico nas duas unidades do serviço, com resposta final diferenciada ao NIR e trilha auditável — o estágio do caso entre a decisão médica e a ciência do NIR.

## Requirements

### Requirement: Fila do agendador completa

O sistema SHALL exibir ao papel ativo `scheduler`/`admin` as abas `Pendentes` (`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`) e `Processados Hoje` — decisões de agendamento (confirmação ou negação) registradas no dia corrente **por qualquer agendador (equipe), independentemente do status atual do caso e de quem processou**. Pendentes permanecem ordenadas por `days_on_screen` desc nulls-last e FIFO; Processados Hoje SHALL ser ordenada pela data/hora da decisão de agendamento, mais recente primeiro, paginada, sem filtro de unidade e exibindo o decisor (nome legível) no card. Pendentes terá filtro server-side opcional por tipo de procedimento, busca client-side e cards 8/12 + tempo/ação 4/12 em desktop, com espera única, decisões médicas por procedimento e ações de reserva. Guards, partial a cada 20 s, contadores, unidade exibida quando definida e apresentação da aba processada são preservados; uid nunca é exibido. Casos decididos em dias anteriores NÃO aparecem nas abas — a consulta de casos antigos é atendida pelo «Histórico do agendador».

#### Scenario: Agendador vê a fila completa

- **GIVEN** casos em `SCHEDULER_REQUESTED` de subtipos distintos e um caso reaberto por intercorrência em `AWAITING_SCHEDULING`
- **WHEN** o agendador acessa a fila
- **THEN** todos são listados na aba de aguardando, ordenados por tempo de tela (desempate FIFO), sem filtro de unidade

#### Scenario: Papel não agendador recebe 403

- **GIVEN** usuário com papel ativo `doctor`
- **WHEN** acessa a fila do agendador
- **THEN** recebe HTTP 403

#### Scenario: Card do agendador com identificação e tempo

- **GIVEN** um caso em `AWAITING_SCHEDULING` com nome, idade 84, sexo, origem e `days_on_screen` 6
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o card exibe nome (`84 a`), ocorrência e demografia disponível, o badge «Dias em tela: 6» e exatamente um rótulo «Aguardando há …», sem repetir a data de recebimento

#### Scenario: Fila do agendador ordenada pelo tempo de tela

- **GIVEN** casos aguardando com `days_on_screen` 10, 3 e um sem cabeçalho (upload mais antigo)
- **WHEN** o agendador acessa a fila
- **THEN** a ordem é 10, 3 e o caso sem cabeçalho ao fim (desempate FIFO), independentemente do momento do upload

#### Scenario: Processados Hoje limita-se às decisões do dia corrente

- **GIVEN** um caso com decisão de agendamento hoje e outro caso decidido ontem ainda não encerrado
- **WHEN** o agendador acessa a aba processados
- **THEN** apenas o caso decidido hoje aparece, ordenado pela data/hora da decisão (mais recente primeiro); o caso de ontem não aparece em nenhuma aba e permanece consultável pelo «Histórico do agendador»

#### Scenario: Processados Hoje inclui apenas as próprias decisões

- **GIVEN** decisões de agendamento registradas hoje por outro agendador
- **WHEN** o agendador autenticado acessa a aba processados
- **THEN** elas são listadas com o decisor visível no card — a aba é da equipe de agendamento, não do decisor

#### Scenario: Aba processada usa rótulo de recebimento

- **GIVEN** um caso já processado pelo agendamento com nome e idade
- **WHEN** o agendador acessa a aba processados
- **THEN** o card exibe o rótulo «Recebido há …», o decisor da decisão e NÃO exibe «Aguardando», com a idade junto do nome

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a fila é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Card pendente mostra decisões por procedimento

- **GIVEN** um caso pronto para agendamento com um procedimento aprovado e outro negado
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o resumo do card exibe cada procedimento, seu subtipo e sua disposição médica, sem agregar as decisões em um resultado único

#### Scenario: Caso reservado por outro agendador aparece bloqueado

- **GIVEN** um caso pronto para agendamento com reserva ativa de outro agendador (unidade distinta)
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o card exibe «Reservado» desabilitado com o nome do portador, sem ação livre ou link de abertura

#### Scenario: Continuar a própria reserva do agendador

- **GIVEN** um caso pronto para agendamento com reserva ativa do próprio agendador
- **WHEN** ele acessa a aba aguardando
- **THEN** o card exibe a ação «Continuar» que reabre a página de trabalho do agendamento

#### Scenario: Caso livre oferece agendar

- **GIVEN** um caso pronto para agendamento sem reserva
- **WHEN** o agendador acessa a aba aguardando
- **THEN** o card exibe o botão primário «Agendar», alinhado à região de ação, que abre a página de trabalho

#### Scenario: Filtro por tipo de procedimento é server-side

- **GIVEN** casos pendentes com tipos declarados distintos
- **WHEN** o agendador seleciona um tipo de procedimento
- **THEN** somente casos com esse tipo declarado são listados, antes da paginação, sem restringir a fila por unidade

#### Scenario: Filtro de procedimento inválido é ignorado

- **GIVEN** a aba aguardando
- **WHEN** `procedure_type` contém valor fora do catálogo
- **THEN** o valor é ignorado e a fila completa é exibida, sem erro e sem ampliar o escopo para outras abas

#### Scenario: Partial atualiza a fila sem reload

- **GIVEN** a fila aberta e um novo caso chegando à fila de agendamento, compatível com o tipo ativo e posicionado na página exibida
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba, tipo de procedimento, busca e paginação

#### Scenario: Caso de outra página não aparece

- **GIVEN** a fila com filtro de procedimento e um novo caso de outro tipo, ou um caso posicionado em outra página
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece na página exibida

#### Scenario: Busca client-side filtra os cards

- **GIVEN** a fila com vários casos renderizados
- **WHEN** o agendador digita o nome do paciente ou o nº de ocorrência na busca
- **THEN** apenas os cards correspondentes permanecem visíveis, sem nova requisição e sem alterar o escopo server-side

#### Scenario: Badges de contagem nas abas

- **GIVEN** 2 casos aguardando e 4 decisões de agendamento do dia corrente (de qualquer agendador)
- **WHEN** a fila é renderizada
- **THEN** as abas exibem as contagens 2 e 4

#### Scenario: Título, alerta e card seguem o modelo operacional

- **GIVEN** a aba aguardando com casos
- **WHEN** ela é renderizada em desktop
- **THEN** o título é «Solicitações de agendamento aguardando confirmação», a contagem usa alerta informativo, e cada card apresenta conteúdo em 8/12 e tempo/ação em 4/12 à direita, numa única superfície e na largura do container do header

#### Scenario: Estado vazio pendente é informativo

- **GIVEN** a aba aguardando sem casos no escopo atual
- **WHEN** ela é renderizada
- **THEN** um alerta informativo informa que nenhuma solicitação aguarda agendamento, sem alerta de warning nem um segundo card vazio

#### Scenario: Layout permanece utilizável no mobile

- **GIVEN** um card com múltiplos procedimentos, decisões e ação
- **WHEN** a fila é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, a ação permanece acessível com alvo mínimo de 44 px e não há overflow horizontal
### Requirement: Confirmação de agendamento com unidade e resposta final

O sistema SHALL confirmar o agendamento de um caso em `SCHEDULER_REQUESTED` ou `AWAITING_SCHEDULING` registrando unidade (1 ou 2), data/hora e local, encadeando as transições até `FINAL_REPLY_POSTED` no mesmo atomic (na origem `AWAITING_SCHEDULING` a transição de entrada de fila não é re-disparada) e postando na thread do caso a resposta final ao NIR: para unidade 1, o texto padrão com local e data/hora; para unidade 2, o texto de recusa com encaminhamento à unidade 2 configurada/Secretaria ("Recusar o relatório — caso agendado na {label da unidade 2 configurada}, que comunicará a Secretaria"; com os defaults de ambiente: "Recusar o relatório — caso agendado na Unidade 2, que comunicará a Secretaria"). Em ambas as unidades, a resposta final SHALL encerrar com a linha do médico responsável pela decisão do caso: `Médico responsável: {nome do médico decisor}` seguido do registro de conselho formatado quando existir (`Médico responsável: {nome} — {conselho} {número}`; ex.: `CRM 12345`); sem registro preenchido, apenas o nome; sem médico decisor registrado, a linha é omitida. Data/hora no passado é rejeitada. A abertura da página de confirmação reserva o caso (contexto próprio do agendador) e a submissão exige reserva vigente (token de lock), liberando-a ao concluir com sucesso.

#### Scenario: Confirmação na unidade 1 publica resposta com data

- **GIVEN** um caso em `SCHEDULER_REQUESTED` decidido por um médico com conselho `CRM` e número preenchidos
- **WHEN** o agendador confirma com unidade 1, data/hora futura e local
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com os campos de agendamento persistidos e a thread contém a resposta final com local, data/hora e a última linha `Médico responsável: {nome} — CRM {número}`

#### Scenario: Confirmação na unidade 2 publica recusa ao relatório

- **GIVEN** um caso em `SCHEDULER_REQUESTED` decidido por um médico com registro preenchido
- **WHEN** o agendador confirma com unidade 2
- **THEN** a resposta final postada é o texto de recusa com encaminhamento à unidade 2 configurada/Secretaria seguido da última linha do médico responsável com o registro

#### Scenario: Médico sem registro preenchido degrada para nome apenas

- **GIVEN** um caso em `SCHEDULER_REQUESTED` decidido por um médico sem conselho/número preenchidos
- **WHEN** o agendador confirma o agendamento em qualquer unidade
- **THEN** a resposta final termina com `Médico responsável: {nome}` sem registro, e a postagem não falha

#### Scenario: Data no passado é rejeitada

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** a confirmação usa data/hora no passado
- **THEN** a submissão é rejeitada com erro de validação e nada é persistido

#### Scenario: Confirmação sem reserva vigente falha

- **GIVEN** um caso em `SCHEDULER_REQUESTED` cujo token de reserva está ausente, inválido ou expirado
- **WHEN** o agendador envia a confirmação
- **THEN** o formulário é reexibido com mensagem de reserva expirada e nada é persistido


### Requirement: Negação de agendamento com motivo obrigatório

O sistema SHALL permitir negar o agendamento de um caso pronto (`SCHEDULER_REQUESTED` ou `AWAITING_SCHEDULING`), exigindo motivo não vazio, encadeando as transições até `FINAL_REPLY_POSTED` no mesmo atomic e postando ao NIR a resposta final com o motivo informado.

#### Scenario: Negação publica motivo ao NIR

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** o agendador nega com motivo informado
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com o motivo persistido e a resposta final ao NIR contém o motivo

#### Scenario: Negação sem motivo é rejeitada

- **GIVEN** um caso em `SCHEDULER_REQUESTED`
- **WHEN** o agendador nega com motivo em branco
- **THEN** o formulário rejeita com erro de validação e nada é persistido


### Requirement: Intercorrência pós-agendamento apenas na unidade 1

O sistema SHALL permitir desmarcar, por intercorrência, um caso confirmado na unidade 1 que ainda não teve ciência do NIR: motivo obrigatório, transição de reabertura para `AWAITING_SCHEDULING` no mesmo atomic, limpeza dos dados de agendamento, evento auditável e comunicação ao NIR informando o retorno à fila. Casos agendados na unidade 2 SHALL ter a intercorrência desabilitada; após re-confirmação o ciclo pode se repetir.

#### Scenario: Intercorrência na unidade 1 retorna o caso à fila

- **GIVEN** um caso em `FINAL_REPLY_POSTED` confirmado na unidade 1
- **WHEN** o agendador registra intercorrência com motivo
- **THEN** o caso volta a `AWAITING_SCHEDULING` com os dados de agendamento limpos, evento na trilha e comunicação ao NIR sobre o retorno à fila

#### Scenario: Intercorrência na unidade 2 é bloqueada

- **GIVEN** um caso em `FINAL_REPLY_POSTED` confirmado na unidade 2
- **WHEN** o agendador tenta registrar intercorrência
- **THEN** a operação é recusada com erro nomeado e o caso permanece inalterado

#### Scenario: Reconfirmação após intercorrência

- **GIVEN** um caso reaberto por intercorrência em `AWAITING_SCHEDULING`
- **WHEN** o agendador confirma novo agendamento na unidade 1
- **THEN** o caso chega a `FINAL_REPLY_POSTED` com os novos dados e nova resposta final ao NIR


### Requirement: Visão do agendador limitada ao necessário

O detalhe do caso para o agendador SHALL exibir identificação do paciente (nome, nascimento, ocorrência), sumário clínico completo re-identificado apenas na renderização, procedimentos com decisões médicas e motivos, dados de agendamento, thread e trilha compartilhada ao final em card collapsible fechado por padrão, com rótulos, badges, ator/papel/data. Não SHALL exibir estrutura extraída, alertas da policy ou sugestão. Título e breadcrumb SHALL usar referência humana (nº ou `uid <8 chars>`), sem uid completo visível. PDF ORIGINAL e texto extraído continuam disponíveis apenas após decisão de agendamento em estado pós-decisão, para qualquer agendador da equipe, por documento ordenado, nos cards padronizados. EXCEÇÃO: relatórios e anexos de reapresentações efetivamente vinculadas SHALL ser acessíveis em seção separada enquanto o original aguarda agendamento e posteriormente no detalhe autorizado, inclusive Histórico do Agendador de canônico CLEANED retido, sem exigir decisão de agendamento do incoming; essa exceção não abre PDF original nem documentos de outro caso não vinculado.

#### Scenario: Agendador vê o diagnóstico resumido re-identificado
- **GIVEN** sumário de múltiplas linhas com pseudônimos e mapa do caso
- **WHEN** agendador abre detalhe
- **THEN** vê sumário completo re-identificado, sem tokens, preservando linhas

#### Scenario: Agendador não vê artefatos clínicos além do permitido
- **WHEN** abre caso com estrutura, policy e sugestão persistidas
- **THEN** identificação, sumário e decisões aparecem e os demais artefatos não aparecem

#### Scenario: PDF disponível apenas de caso processado pelo próprio agendador
- **GIVEN** caso confirmado por qualquer agendador com dois documentos
- **WHEN** qualquer agendador pede cada PDF original
- **THEN** cada documento é servido como PDF

#### Scenario: PDF negado a outro agendador ou a caso reaberto
- **GIVEN** caso reaberto por intercorrência, fora do gate pós-decisão
- **WHEN** agendador pede PDF original
- **THEN** recebe 404; isso não impede acesso separado a reapresentações efetivamente vinculadas

#### Scenario: Detalhe do agendador usa a referência humana
- **WHEN** abre detalhe de caso com nº
- **THEN** título e breadcrumb mostram nº, com fallback uid curto quando ausente e uid completo apenas nos hrefs

#### Scenario: Texto extraído sob o mesmo gate do PDF
- **WHEN** abre detalhe com texto extraído
- **THEN** texto e PDF ORIGINAL aparecem somente sob gate pós-decisão, sem estender esse gate pela existência de reapresentação

#### Scenario: Trilha do agendador mostra quem decidiu e quem criou
- **GIVEN** caso criado por NIR e decidido por médico
- **WHEN** agendador expande trilha ao final
- **THEN** criação e decisão mostram ator, papel e data; card nasce fechado e sumário completo aparece acima

#### Scenario: PDF e texto de caso processado por outro agendador da equipe
- **GIVEN** caso confirmado por outro agendador em estado pós-decisão
- **WHEN** agendador diferente abre detalhe
- **THEN** cards de texto e PDF aparecem e o PDF original é servido normalmente

### Requirement: Contador e documentos de reapresentações no agendamento

O card pendente e seu partial SHALL mostrar quantidade de reapresentações aplicadas e link para seção do detalhe. A seção SHALL separar cada envio com data, responsável, regulação, relatório e anexos disponíveis. Documentação recebida não SHALL substituir relatório aprovado nem reiniciar espera, suspender agenda ou exigir triagem de atualização clínica.

#### Scenario: Contagem de envios distintos
- **WHEN** três incoming distintos são vinculados ao original e um job é repetido
- **THEN** card e partial mostram três reapresentações, último recebimento e link funcional para os documentos

#### Scenario: Página de trabalho mantém acesso
- **WHEN** agendador abre `scheduler:case_detail`, página de trabalho com os formulários POST de confirmação e negação
- **THEN** pode consultar seção de reapresentações sem perder reserva nem abrir outro caso operacional

#### Scenario: Relatório e anexos recebidos
- **WHEN** reapresentação contém PDF e anexos PDF/imagem
- **THEN** seção permite abrir os arquivos disponíveis, identificados por envio e sem afirmar aprovação médica da nova documentação

#### Scenario: Espera herdada chega ao agendador
- **WHEN** caso substituído na fila médica é aprovado
- **THEN** card, partial e desempate FIFO do agendamento usam marco herdado, mantendo dias em tela como prioridade principal e criação real intacta

#### Scenario: Histórico retido mantém documentos recebidos
- **WHEN** canônico CLEANED em retenção é aberto em `scheduler:historical_case_detail`
- **THEN** seção de reapresentações exibe relatórios/anexos ainda existentes em leitura, sem liberar PDF original nem ações de agendamento

#### Scenario: Documentos removidos não são prometidos como disponíveis
- **WHEN** dados de grupo foram minimizados ou purgados
- **THEN** histórico não oferece abertura funcional de arquivos removidos e serve/viewer retornam 404
### Requirement: Rótulos de unidade configuráveis por ambiente

O sistema SHALL exibir os nomes das unidades de agendamento a partir das variáveis de ambiente `HMD_UNIT_1_LABEL` e `HMD_UNIT_2_LABEL` (defaults "Unidade 1" e "Unidade 2"), com fonte única de resolução, em todos os pontos de exibição: choices do formulário de confirmação, resposta final ao NIR da unidade 2, painel, detail do caso e manual do usuário. O valor persistido de agendamento continua sendo o código da unidade (1 ou 2); labels não alteram validação, FSM ou dados históricos já gravados.

#### Scenario: Labels configurados refletem em toda a exibição

- **GIVEN** `HMD_UNIT_1_LABEL=Hemodinâmica HGRS` e `HMD_UNIT_2_LABEL=Unidade Satélite`
- **WHEN** um agendador abre o formulário de confirmação, confirma um caso na unidade 2, e o NIR/manager visualiza o painel e o manual
- **THEN** o formulário, a resposta final postada, o painel e o manual exibem os labels configurados (a resposta final da unidade 2 cita "Unidade Satélite")

#### Scenario: Defaults mantêm o comportamento atual

- **GIVEN** as variáveis de ambiente de labels não definidas (ou vazias)
- **WHEN** qualquer fluxo de agendamento é executado
- **THEN** todas as exibições usam "Unidade 1"/"Unidade 2" e os textos permanecem idênticos aos atuais


### Requirement: Reserva de caso durante o agendamento

O sistema SHALL reservar o caso para o agendador quando ele abre a página de confirmação ou de negação (`SCHEDULER_REQUESTED`/`AWAITING_SCHEDULING`): claim atômico com contexto próprio e lease renovável por heartbeat enquanto há atividade. Reserva ativa de outro portador SHALL barrar a abertura com mensagem identificando o portador e retorno à fila; a reserva SHALL ser liberada imediatamente ao sair sem decidir ou ao concluir a confirmação/negação, e SHALL expirar sozinha sem renovação. O mesmo agendador reabrindo renova a reserva com novo token.

#### Scenario: Abrir a confirmação reserva o caso

- **GIVEN** um caso em `SCHEDULER_REQUESTED` sem reserva
- **WHEN** o agendador abre a página de confirmação
- **THEN** o caso fica reservado para ele (evento de claim na trilha) e o formulário carrega o token da reserva

#### Scenario: Outro agendador é barrado pela reserva ativa

- **GIVEN** um caso pronto para agendamento reservado pelo agendador da unidade 1
- **WHEN** o agendador da unidade 2 tenta abrir a confirmação
- **THEN** ele recebe mensagem de que o caso está reservado (com o portador) e é redirecionado à fila, sem alterar a reserva

#### Scenario: Sair sem decidir libera imediatamente

- **GIVEN** um caso pronto para agendamento reservado pelo agendador
- **WHEN** ele aciona «Sair sem decidir»
- **THEN** a reserva é liberada na hora e ele volta à fila com o caso disponível

### Requirement: Histórico do agendador

O sistema SHALL oferecer ao papel ativo `scheduler`/`admin` uma página de histórico com
busca server-side por nº de ocorrência OU nome do paciente sobre os casos que passaram
por decisão de agendamento (confirmação, negação ou cancelamento por intercorrência),
incluindo casos encerrados (`CLEANED`) e casos com intercorrência ativa, de qualquer
médico e agendador — com no máximo 50 resultados por consulta, estado vazio sem critério
e link para o detalhe do caso no escopo histórico (leitura, sem ações de agendamento).
A busca SHALL usar a referência humana nos resultados e o uid completo NÃO aparece em
texto visível.

#### Scenario: Busca por nome encontra caso antigo encerrado

- **GIVEN** um caso `CLEANED` decidido há vários dias, cujo nome do paciente contém o termo buscado
- **WHEN** o agendador busca pelo nome no histórico
- **THEN** o caso é listado com referência humana, status legível e dados do agendamento anterior, mesmo não estando mais em nenhuma aba da fila

#### Scenario: Busca por número de ocorrência

- **GIVEN** casos com nº de ocorrência distinto
- **WHEN** o agendador busca pelo nº
- **THEN** apenas os casos correspondentes são listados, incluindo casos de outros agendadores

#### Scenario: Sem critério mantém estado vazio

- **GIVEN** a página de histórico aberta sem termo de busca
- **WHEN** renderizada
- **THEN** nenhum resultado é exibido e um estado vazio orienta o uso da busca

#### Scenario: Detalhe histórico sem ações de agendamento

- **GIVEN** um resultado de busca no histórico
- **WHEN** o agendador abre o detalhe do caso
- **THEN** o detalhe é exibido em leitura (mesmo conteúdo do detalhe do agendador) sem botões de confirmação, negação ou desmarcação, e o PDF/texto extraído seguem o gate de equipe — estados pós-decisão de agendamento com decisão registrada por qualquer decisor (decisão do dono 2026-11-22)

#### Scenario: Negativa médica encerrada não entra no histórico

- **GIVEN** um caso `CLEANED` que foi negado pelo médico sem nunca passar por decisão de agendamento
- **WHEN** o agendador busca pelo nome ou nº dele no histórico
- **THEN** o caso não é listado — o universo do histórico exige decisão de agendamento registrada

#### Scenario: Papel não agendador recebe 403

- **GIVEN** usuário com papel ativo `nir`
- **WHEN** acessa a página de histórico do agendador
- **THEN** recebe HTTP 403
### Requirement: Badge de transporte nos cards da fila do agendador

Os cards da fila do agendador (abas Pendentes e Processados Hoje) SHALL exibir um badge com o tipo de transporte do caso quando presente: `UTI Móvel` com ênfase visual de gravidade e `Ambulância Básica` em estilo neutro, com rótulos canônicos — mesma apresentação das demais filas (paridade de sinal entre papéis). Caso sem transporte extraído não exibe badge.

#### Scenario: Card com UTI Móvel exibe badge de ênfase

- **GIVEN** um caso em `AWAITING_SCHEDULING` com tipo de transporte `mobile_icu`
- **WHEN** o agendador acessa a aba Pendentes
- **THEN** o card exibe o badge «UTI Móvel» em estilo de ênfase de gravidade, junto da demografia disponível

#### Scenario: Card com Ambulância Básica exibe badge neutro

- **GIVEN** um caso na fila do agendador com tipo de transporte `basic`
- **WHEN** o agendador acessa a fila
- **THEN** o card exibe o badge «Ambulância Básica» em estilo neutro

#### Scenario: Card sem transporte não exibe badge

- **GIVEN** um caso na fila do agendador sem transporte extraído
- **WHEN** o agendador acessa a fila
- **THEN** o card não exibe badge de transporte, sem placeholder
