# Spec Delta

## MODIFIED Requirements

### Requirement: Confirmação explícita do procedimento pelo NIR

O NIR criador SHALL poder manter ou trocar o procedimento de um caso novo retido por divergência, escolhendo exatamente um dos 14 tipos do catálogo, inclusive não detectado. A ação SHALL exigir leitura confirmada e justificativa não vazia de até 500 caracteres, além de revisão e fonte vigentes. A confirmação SHALL solicitar nova extração e análise do mesmo caso, sem exigir upload nem aprovar clinicamente o pedido.

#### Scenario: Manter procedimento original é permitido
- **GIVEN** caso novo com A declarado e B detectado
- **WHEN** o NIR mantém A, justifica e confirma leitura
- **THEN** a confirmação é aceita e solicita reprocessamento completo para A, sem exigir diferença de seleção

#### Scenario: Escolher procedimento não detectado é permitido
- **GIVEN** caso novo com A declarado e B detectado
- **WHEN** o NIR escolhe C válido, não detectado, justifica e confirma leitura
- **THEN** C torna-se o único procedimento declarado para análise no mesmo caso, sem modificar o relatório

#### Scenario: Seleção e consentimento são validados no servidor
- **GIVEN** revisão elegível
- **WHEN** o POST omite leitura, não informa seleção, envia tipo desconhecido, envia múltiplos tipos ou justificativa vazia, apenas espaços, com NUL ou acima de 500 caracteres
- **THEN** nada é confirmado, alterado ou enfileirado e a tela informa o erro

#### Scenario: Limite da justificativa é aceito
- **GIVEN** seleção única válida e leitura confirmada
- **WHEN** a justificativa após remoção de espaços nas extremidades tem exatamente 500 caracteres
- **THEN** a confirmação é aceita sem truncamento silencioso

#### Scenario: Revisão ou fonte obsoleta exige reabrir o caso
- **GIVEN** formulário aberto antes de mudança da revisão, dos bytes/texto da fonte principal, do texto anonimizado ou do mapa de pseudônimos
- **WHEN** o NIR submete a confirmação antiga
- **THEN** a ação é recusada com 400, sem confirmação, job ou mudança de estado; revisão obsoleta com fonte íntegra orienta reabrir, enquanto fonte/texto anonimizado/mapa inconsistente orienta suporte; nome de arquivo igual não autoriza conteúdo diferente

#### Scenario: Apenas criador com papel ativo NIR confirma
- **GIVEN** usuário alheio ao caso ou com papel ativo não NIR
- **WHEN** tenta o POST direto
- **THEN** acesso é negado sem confirmação, mutação ou dispatch

#### Scenario: Formulário funciona sem JavaScript
- **GIVEN** revisão elegível com JavaScript indisponível
- **WHEN** o NIR escolhe no select canônico, justifica e confirma leitura
- **THEN** o servidor recebe o tipo selecionado e processa a mesma confirmação

#### Scenario: Erro de formulário preserva tentativa sem consentimento implícito
- **GIVEN** submissão inválida da revisão
- **WHEN** a página é reexibida
- **THEN** seleção e justificativa são preservadas e escapadas, os erros ficam visíveis e a leitura não é marcada automaticamente
