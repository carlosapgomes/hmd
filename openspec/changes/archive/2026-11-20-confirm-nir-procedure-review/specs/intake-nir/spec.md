# Spec Delta

## MODIFIED Requirements

### Requirement: Revisão NIR do gate

O NIR SHALL conseguir revisar casos retidos pelo gate na tela de detalhe. Para retenção de formato, as ações permanecem **liberar** (bypass para `ANONYMIZING` com evento) e **reenviar documento** (substituição por exatamente 1 PDF e reprocessamento da extração). Para divergência de procedimento dos novos envios, a resolução SHALL exigir confirmação explícita do procedimento, não bypass simples nem novo upload. Casos anteriores à implantação não SHALL ser convertidos ao novo fluxo. As ações dependem da retenção correspondente estar vigente e do caso pertencer ao NIR.

#### Scenario: Liberar avança com evento de bypass
- **GIVEN** um caso retido em `PDF_EXTRACTING` com `manual_review_required=True`
- **WHEN** o NIR libera o caso
- **THEN** o caso avança para `ANONYMIZING` e a trilha registra o evento de bypass com o NIR como ator

#### Scenario: Reenviar documentos reprocessa do zero
- **GIVEN** um caso retido com documento inválido
- **WHEN** o NIR reenvia exatamente 1 PDF válido
- **THEN** o documento antigo é substituído, a flag de revisão é zerada, o texto anterior é descartado e o caso é reprocessado

#### Scenario: Reenvio do gate exige exatamente um PDF
- **GIVEN** um caso retido por formato
- **WHEN** o NIR reenvia 0 ou mais de 1 arquivo
- **THEN** nada é alterado e o erro nomeado informa que o reenvio aceita exatamente 1 PDF

#### Scenario: Bypass antigo não resolve divergência nova
- **GIVEN** caso enviado sob o novo contrato e retido por divergência
- **WHEN** é submetido apenas o POST de liberação sem confirmação explícita
- **THEN** a ação é recusada e o caso continua retido, sem job, confirmação ou encaminhamento médico

#### Scenario: Divergência não autoriza reenvio pelo gate de formato
- **GIVEN** caso retido por divergência de procedimento
- **WHEN** é tentado reenvio pela ação exclusiva de formato
- **THEN** a tentativa é recusada sem substituir arquivos

### Requirement: Meus casos e detalhe do NIR

O NIR SHALL ver, numa lista "meus casos", apenas os casos criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir a identificação do paciente (nome e idade, padrão da lista) num card «Identificação do paciente» com badge de status no header, além de documentos (com visualização do PDF) e comunicações — **sem a trilha de eventos** (a trilha vive no painel; caso `FAILED` exibe badge de erro «Falha no processamento»). Acesso a caso criado por outro usuário SHALL ser negado. Os cards NÃO exibem o uid completo do caso — a única exceção é o sufixo curto de desambiguação (`· <8 chars>`), exibido apenas no cenário de nº duplicado descrito abaixo. Toda referência textual a um caso no âmbito do NIR — `<title>`, breadcrumb, cabeçalho do detalhe, mensagens flash e alerta de lote — SHALL usar a **referência humana**: nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel); o uid completo NÃO é exibido em texto visível (permanece apenas como chave das URLs). A linha rotulada «Nº de ocorrência» dos cards mantém `—` quando o nº está ausente (decisão vigente de `queues-remove-uid`: card sem nº fica apenas com a identificação do paciente) — o fallback em `uid <8 chars>` aplica-se a referências (títulos, breadcrumbs, cabeçalho do detalhe, mensagens), não a essa linha rotulada. Onde dois casos compartilham o mesmo nº (reenvio corrigido cria caso novo), a referência SHALL ser desambiguada com sufixo curto (`nº · <8 chars>`): link de «Reenvios corrigidos» no detalhe, página «Reenviar corrigido», flash do novo caso e cards de «meus casos» quando a lista contém nº duplicado. O detalhe exibe TODAS as rows de procedimento do caso com a origem («Declarado» NIR / «Detectado na extração») e o status de detecção após a reconciliação; no card de revisão por divergência, o resumo declarados × detectados acompanha a confirmação explícita nos novos envios e a liberação no contrato histórico.

Meus casos terá página e lista na largura do header, navegação Ativos/Encerrados apenas no header, filtros server-side preservados, contagem neutra e cards compactos em quatro regiões no desktop (4/3/3/2), sem alerta de espera ou resumo redundante. O partial, os contadores e o encerramento administrativo são preservados.

#### Scenario: Lista mostra apenas casos do próprio NIR
- **GIVEN** dois NIR com casos criados
- **WHEN** um deles abre meus casos
- **THEN** vê somente os casos que ele criou, com status, flag de retenção e identificação do paciente visíveis

#### Scenario: Detalhe de caso alheio é negado
- **GIVEN** um caso criado por outro NIR
- **WHEN** o NIR tenta abrir o detalhe diretamente
- **THEN** o acesso é negado (404) sem vazar informação

#### Scenario: Detalhe exibe documentos e trilha
- **GIVEN** um caso do próprio NIR em processamento
- **WHEN** abre o detalhe
- **THEN** vê os PDFs enviados (servidos de forma segura) e as comunicações do caso, sem a trilha de eventos

#### Scenario: Card de meus casos identifica o paciente
- **GIVEN** um caso criado pelo NIR com nome, idade, sexo e origem extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe nome, idade (`84 a`), ocorrência e demografia disponível; sem nome exibe `—` e omite idade

#### Scenario: Caso FAILED exibe badge de erro no detalhe do NIR
- **GIVEN** um caso do próprio NIR em `FAILED`
- **WHEN** o NIR abre o detalhe
- **THEN** um badge de erro «Falha no processamento» é exibido, sem a trilha de eventos nem o motivo técnico

#### Scenario: Cards sem o identificador interno do caso
- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a lista é renderizada
- **THEN** nenhum card exibe o uid do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores visíveis

#### Scenario: Detalhe retido por divergência lista declarados e detectados
- **GIVEN** um caso novo do próprio NIR retido em `LLM_EXTRACTING` por divergência (`angio_art_perif` declarada não-detectada e `art_perif` detectada não-declarada)
- **WHEN** o NIR abre o detalhe
- **THEN** a seção de procedimentos exibe ambas as rows com rótulos legíveis e badges de origem/detecção e o card resume declarados × detectados junto ao formulário de confirmação, sem bypass simples

#### Scenario: Detalhe do NIR identifica o paciente sem uid completo
- **GIVEN** um caso do próprio NIR com nome, idade e nº de ocorrência extraídos
- **WHEN** o NIR abre o detalhe
- **THEN** o card «Identificação do paciente» exibe nome, idade e nº de ocorrência com badge de status no header; `<title>`, breadcrumb e cabeçalho citam a referência humana («Caso <nº>») e o uid completo não aparece em texto visível (apenas em hrefs)

#### Scenario: Detalhe sem nº de ocorrência usa o fallback uid curto
- **GIVEN** um caso do próprio NIR sem nº de ocorrência (retido no gate)
- **WHEN** o NIR abre o detalhe
- **THEN** `<title>`, breadcrumb e cabeçalho exibem `uid <8 primeiros chars>` no lugar do nº, sem o uid completo

#### Scenario: Mensagens do fluxo citam a referência humana
- **GIVEN** um caso do próprio NIR com nº de ocorrência
- **WHEN** o NIR conclui criação, confirmação de recebimento, liberação de gate, confirmação de procedimento ou reenvio de documentos
- **THEN** a mensagem flash cita a referência humana (nº de ocorrência; fallback `uid <8 chars>` quando ausente), nunca o uid completo

#### Scenario: Referência do reenvio corrigido é desambiguada
- **GIVEN** um caso encerrado com nº N e um reenvio corrigido criado (novo caso com o mesmo nº N)
- **WHEN** o NIR vê o flash de reenvio criado, a página «Reenviar corrigido» do original, e o link do novo caso na lista «Reenvios corrigidos» do detalhe
- **THEN** a referência exibida é `N · <8 primeiros chars do uid>` para distinguir original e novo caso

#### Scenario: Meus casos com nº duplicado exibe sufixo curto
- **GIVEN** dois casos do mesmo NIR compartilhando o nº N (original encerrado + reenvio corrigido)
- **WHEN** o NIR abre «meus casos»
- **THEN** os cards dos casos com nº N exibem o nº acompanhado do sufixo curto (`N · <8 chars>`); cards com nº único permanecem apenas com o nº

#### Scenario: Filtros server-side compõem por AND
- **GIVEN** casos do NIR com nºs, status e tipos distintos
- **WHEN** filtra por nº (≥3 caracteres) + status + tipo de procedimento
- **THEN** apenas os casos que atendem a todos os critérios são listados, com os filtros preservados entre página cheia e partial

#### Scenario: Abas Ativos e Encerrados com contadores
- **GIVEN** 2 casos ativos e 1 encerrado (`CLEANED`) do NIR
- **WHEN** abre meus casos
- **THEN** o header exibe Meus Casos/Casos Encerrados com as contagens 2 e 1, cada destino lista só a situação correspondente e nenhuma segunda pill Ativos/Encerrados aparece no corpo da página

#### Scenario: Troca de aba pelo header preserva filtros
- **GIVEN** meus casos com ocorrência, status e tipo filtrados
- **WHEN** o NIR alterna entre Meus Casos e Casos Encerrados pelo header
- **THEN** o parâmetro `tab` muda e os filtros ativos são preservados na URL e na listagem

#### Scenario: Partial atualiza meus casos sem reload
- **GIVEN** meus casos abertos e um novo caso criado pelo NIR, compatível com os filtros ativos
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando aba e filtros

#### Scenario: Caso fora do filtro não aparece no refresh
- **GIVEN** meus casos filtrados por um status e um novo caso criado em outro status
- **WHEN** decorre o intervalo de atualização
- **THEN** nenhum card novo aparece e os filtros seguem aplicados

#### Scenario: Página e lista têm hierarquia neutra
- **GIVEN** meus casos com resultados
- **WHEN** a página é renderizada
- **THEN** o título é «Todos os seus encaminhamentos», a quantidade aparece em cabeçalho neutro da lista e não existe alerta amarelo de contagem nem CTA «Enviar relatório» duplicado no topo

#### Scenario: Card compacto tem quatro regiões no desktop
- **GIVEN** um caso com procedimento, recebimento, status e detalhe disponível
- **WHEN** o card é renderizado em desktop
- **THEN** identificação ocupa 4/12, recebimento/complementos 3/12, status/alertas 3/12 e «Ver detalhes» 2/12 à direita, numa única superfície, sem `summary-box`, `waiting-time` ou item de lista aninhado

#### Scenario: Status e exceções permanecem visíveis no card compacto
- **GIVEN** casos com andamento, falha, retenção e encerramento administrativo
- **WHEN** os cards são renderizados
- **THEN** cada um mantém status e badges semânticos correspondentes, e o encerramento administrativo conserva resultado e motivo sem recriar uma caixa de resumo

#### Scenario: Estado vazio orienta a próxima ação
- **GIVEN** uma listagem sem resultados
- **WHEN** há filtros ativos
- **THEN** o estado vazio centralizado oferece «Limpar filtros»; sem filtros, informa a ausência da situação da aba e oferece «Novo Encaminhamento» quando aplicável

#### Scenario: Card compacto permanece utilizável no mobile
- **GIVEN** um card com nome longo, múltiplos procedimentos e ação
- **WHEN** a lista é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, «Ver detalhes» ocupa a largura disponível com alvo mínimo de 44 px e não há overflow horizontal

## ADDED Requirements

### Requirement: Confirmação explícita do procedimento pelo NIR

O NIR criador SHALL poder manter ou trocar o procedimento de um caso novo retido por divergência, escolhendo exatamente um dos 13 tipos, inclusive não detectado. A ação SHALL exigir leitura confirmada e justificativa não vazia de até 500 caracteres, além de revisão e fonte vigentes. A confirmação SHALL solicitar nova extração e análise do mesmo caso, sem exigir upload nem aprovar clinicamente o pedido.

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

### Requirement: Revisão de procedimento aplica-se apenas a novos envios

O novo contrato de confirmação SHALL ser atribuído apenas aos casos criados por envios após a implantação, inclusive cada caso de lote e novo reenvio corrigido. Casos anteriores não SHALL receber conversão, marcação retroativa ou interpretação de bypass como confirmação. O envio continua com um PDF por caso e tipo único.

#### Scenario: Novos casos do lote recebem o contrato
- **WHEN** dois relatórios são enviados após a implantação
- **THEN** ambos os novos casos seguem confirmação explícita se ocorrer divergência, independentemente do resultado do outro

#### Scenario: Reenvio corrigido novo não converte o original
- **GIVEN** caso anterior encerrado
- **WHEN** nasce um reenvio corrigido após a implantação
- **THEN** apenas o novo caso recebe o contrato de confirmação e o original permanece inalterado

#### Scenario: Caso anterior não é convertido ao abrir detalhe
- **GIVEN** caso existente antes da implantação
- **WHEN** seu detalhe é aberto ou é tentada a nova confirmação
- **THEN** nenhum marcador ou confirmação é criado e o novo formulário não é oferecido
