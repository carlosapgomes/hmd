# Spec Delta

## ADDED Requirements

### Requirement: Encerramento específico por reapresentação

O sistema SHALL encerrar por reapresentação somente após resolução válida do mesmo pedido, com vínculo e motivo rastreáveis. Esse encerramento SHALL ser distinto de ciência NIR, negativa clínica e encerramento administrativo; não exige simular papel manager/admin nem emite decisão ou resposta final fictícia.

#### Scenario: Encerramento sistêmico legítimo
- **WHEN** resolução médica ou de agendamento é aplicada
- **THEN** caso apropriado chega a CLEANED com vínculo e evento próprio e caso mantido continua operacional

#### Scenario: Disposição depende de quem encerra
- **WHEN** original médico é substituído ou incoming é arquivado no agendamento
- **THEN** original médico segue modo ordinário de retenção/minimização; incoming do agendamento retém dados e marcador de disposição nulo até disposição do grupo, sem apagar arquivos antecipadamente

#### Scenario: Resultado legível
- **WHEN** NIR consulta caso encerrado por reapresentação na busca/detalhe histórico
- **THEN** vê o motivo, elo imediato e link autorizado ao representante atual da cadeia, sem reescrever elos antigos ou apresentar conclusão clínica/negativa inexistente

### Requirement: Proteção dos reenvios vinculados ao ciclo ativo

Documentos e anexos de incoming encerrado por reapresentação de agendamento SHALL permanecer disponíveis enquanto canônico não estiver CLEANED, inclusive em minimização. No encerramento canônico por ciência ou administração, os incoming vinculados SHALL seguir a mesma política de disposição do ciclo: minimização remove dados/arquivos e retenção preserva. Identidade, decisões e auditoria permanecem.

#### Scenario: Minimização não apaga documentação de fila ativa
- **WHEN** incoming encerra vinculado a original ainda aguardando agendamento com minimização ativa
- **THEN** seus relatórios e anexos continuam acessíveis pelas rotas autorizadas

#### Scenario: Encerramento do ciclo minimiza grupo
- **WHEN** canônico encerra por ciência ou administração em minimização
- **THEN** disposição inclui dados clínicos/documentos/anexos dos incoming vinculados, com remoção física após commit

#### Scenario: Retenção preserva grupo
- **WHEN** canônico encerra sob retenção
- **THEN** dados do canônico e incoming permanecem e só purga explícita posterior pode minimizá-los

## MODIFIED Requirements

### Requirement: Purga retroativa dos dados retidos

O sistema SHALL oferecer comando de gerência que reaplica minimização a CLEANED com dados retidos, zerando campos clínicos/LLM e removendo rows e arquivos de documentos/anexos. SHALL registrar auditoria por caso com nomes de campos e contagens, nunca conteúdo clínico ou nomes de arquivos; ser idempotente; suportar --dry-run; reportar falha física por caso e sair com erro se houver. EXCEÇÃO: incoming de reapresentação vinculado a canônico ainda não CLEANED SHALL ser protegido e reportado como tal no dry-run e execução real. Após encerramento do canônico, a purga SHALL alcançar o grupo sem permitir corrida com reabertura. Pedido de purga de incoming cujo canônico CLEANED ainda não foi disposto SHALL purgar o grupo inteiro ou recusar sem escrita; nunca apagar só incoming enquanto canônico continua reabrível. Dry-run SHALL agrupar e deduplicar a mesma unidade de disposição.

#### Scenario: Purga minimiza casos encerrados retidos
- **GIVEN** casos CLEANED retidos sem proteção ativa
- **WHEN** comando executa
- **THEN** campos são zerados, rows/arquivos removidos, auditoria guarda nomes de campos/contagens sem conteúdo e rotas de documento retornam 404

#### Scenario: Falha na deleção física é reportada
- **WHEN** arquivo falha ao ser removido
- **THEN** caso é reportado incompleto com contagem de falhas e comando sai com erro

#### Scenario: Casos ativos não são tocados
- **WHEN** purga executa com casos operacionais presentes
- **THEN** nenhum caso fora de CLEANED é alterado

#### Scenario: Purga é idempotente
- **WHEN** comando executa novamente sobre casos já purgados
- **THEN** não há nova alteração nem eventos duplicados

#### Scenario: Execução a seco não altera nada
- **WHEN** comando executa com --dry-run
- **THEN** relatório lista elegíveis e protegidos sem alterar dados

#### Scenario: Incoming encerrado protegido por canônico ativo
- **WHEN** comando encontra incoming CLEANED vinculado a canônico ativo, inclusive com resposta final ainda sem ciência
- **THEN** informa proteção e não apaga dados ou arquivos daquele incoming

#### Scenario: Purga de incoming com canônico ainda reabrível
- **WHEN** purga é solicitada para incoming e canônico está CLEANED com dados retidos e sem disposição marcada
- **THEN** operação dispõe grupo inteiro e marca canônico atomicamente, ou não remove nada se inelegível; não deixa canônico reabrível com documentação recebida removida

#### Scenario: Reabertura concorrente
- **WHEN** canônico é reaberto enquanto purga tenta avaliar o grupo
- **THEN** a proteção é revalidada sob exclusão mútua e não ocorre purga de grupo com ciclo operacional ativo
