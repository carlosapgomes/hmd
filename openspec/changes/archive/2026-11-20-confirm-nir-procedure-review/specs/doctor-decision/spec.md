# Spec Delta

## ADDED Requirements

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
