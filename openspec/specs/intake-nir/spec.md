# intake-nir Specification

## Purpose

Porta de entrada de casos do HMD: o NIR envia os PDFs do relatório de regulação e declara os tipos de procedimento; o sistema extrai o texto de forma assíncrona — incluindo os metadados do cabeçalho padrão SESAB (idade, sexo, raça/cor, dias em tela) — aplica o gate de regulação adaptado (retendo documentos fora do padrão para revisão) e mantém o NIR informado pelos meus casos.

## Requirements

### Requirement: Envio em lote: um PDF por caso, tipo único

O NIR (papel ativo `nir`) SHALL enviar de 1 a N arquivos PDF num único envio, onde cada PDF é o relatório de um paciente e vira um caso independente. Cada caso SHALL nascer atomicamente (caso em `NEW`, exatamente 1 documento, declaração do único tipo do lote e eventos juntos ou nada). O tipo de procedimento SHALL ser único por envio/lote (validado contra o catálogo; múltiplos tipos não são permitidos). O lote SHALL respeitar limites configurados de contagem de arquivos, tamanho por arquivo e tamanho total, rejeitados com erro claro antes de qualquer persistência. Falhas por arquivo (não-PDF, tamanho) SHALL rejeitar apenas aquele arquivo: os válidos viram casos e o resultado lista os casos criados e os erros por arquivo; exceção de persistência num caso do lote SHALL resultar em erro por arquivo, preservando os casos já criados. Anexos seguem a regra da spec `attachments` (somente com exatamente 1 PDF; no lote multi-PDF com anexos enviados, os casos são criados sem anexos e o erro informa a restrição). O envio com exatamente 1 PDF e anexo inválido SHALL abortar todo o envio (nada criado).

#### Scenario: Lote de dois PDFs cria dois casos independentes

- **GIVEN** um usuário com papel ativo `nir`
- **WHEN** envia 2 PDFs válidos declarando um único tipo do catálogo
- **THEN** dois casos independentes nascem em `NEW`, cada um com exatamente 1 documento e o tipo declarado, com evento de declaração por caso na trilha

#### Scenario: Arquivo inválido no lote rejeita apenas ele

- **GIVEN** um lote com 2 PDFs válidos e 1 imagem (não-PDF)
- **WHEN** o envio é submetido
- **THEN** os 2 casos dos PDFs válidos são criados e o resultado lista o erro nomeado do arquivo inválido, sem interromper os demais

#### Scenario: Tipo único inválido rejeita o lote inteiro

- **GIVEN** um envio com tipo fora do catálogo (ou mais de um tipo)
- **WHEN** o envio é submetido
- **THEN** nenhum caso é criado e o erro informa a restrição de tipo único

#### Scenario: Limites do lote excedidos rejeitam tudo

- **GIVEN** a configuração de limites de contagem/tamanho do lote
- **WHEN** o envio excede arquivos por lote ou tamanho total
- **THEN** nada é criado e o erro informa o limite violado antes de qualquer persistência

#### Scenario: Enviar um único PDF com anexo inválido aborta tudo

- **GIVEN** um envio com exatamente 1 PDF válido e 1 anexo fora dos limites
- **WHEN** o envio é submetido
- **THEN** nenhum caso é criado e o erro nomeado do anexo é retornado

#### Scenario: Lote multi-PDF com anexos cria casos sem anexos

- **GIVEN** um envio com 2 PDFs válidos e anexos
- **WHEN** o envio é submetido
- **THEN** os 2 casos são criados sem anexos e o resultado informa que anexos só são permitidos com exatamente 1 relatório

#### Scenario: Cada caso do lote é atomicamente criado

- **GIVEN** um lote em que o 2º arquivo falha na persistência após o 1º caso já criado
- **WHEN** o envio é processado
- **THEN** o 1º caso permanece criado e íntegro e o resultado lista o erro do 2º arquivo

### Requirement: Extração assíncrona no cluster pdf

A criação SHALL enfileirar o processamento do caso no cluster `pdf` (django-q2); o processamento SHALL extrair o texto de cada documento com PyMuPDF na ordem declarada, concatenando em `Case.extracted_text` (fonte única de texto, com quebra de linha separando o fim de cada página), remover a marca d'água característica e extrair o número de ocorrência para `agency_record_number` quando presente. A marca d'água é um token de 5–6 dígitos — frequentemente DISTINTO do nº de ocorrência — nomeado quando aparece em ao menos 2 **linhas de marca**: linha composta exclusivamente de repetições do token (1 a N repetições separadas por espaço, nada mais na linha — cobre banda cheia, diagonal com poucas repetições e linha solta). As linhas de marca do token nomeado são descartadas; linha única não nomeia. O sweep além dessas linhas fica restrito ao token que coincide com o nº de ocorrência (removido em qualquer ocorrência delimitada do texto — o valor vive em `agency_record_number`); ocorrências de outros tokens nomeados no meio de linhas com outro conteúdo SHALL ser preservadas. Texto sem marca nomeada SHALL permanecer inalterado. A telemetria de remoção no evento de extração (sob a chave `watermark`) SHALL carregar somente contagens (tokens nomeados, linhas de marca descartadas, ocorrências varridas), jamais valores de token; o campo `record_number` pré-existente do evento (referência humana do caso, comportamento estabelecido antes deste change) permanece inalterado e fora dessa proibição — inclusive quando o token de marca coincide com o nº. O processamento SHALL ainda extrair os metadados do cabeçalho padrão SESAB (repetido por página) quando presentes — idade (`Idade: N a.` na linha de demografia), sexo, raça/cor e dias em tela (maior ocorrência de `Dias em tela: N`, com o valor na mesma linha OU na linha imediatamente seguinte quando o rótulo está sozinho — layout real do cabeçalho) e a **unidade de origem** (`Unid. Origem:`, valor na mesma linha OU na linha imediatamente seguinte) — persistindo-os nos campos do caso (`patient_age`/`patient_gender`/`patient_race`/`days_on_screen`/`origin_unit`), sem carregar esses valores em eventos. Campos ausentes do cabeçalho ficam vazios no caso. O ciclo SHALL percorrer `NEW → PDF_EXTRACTING → ANONYMIZING` com eventos de ator sistema, operando sob lock do caso (contexto de worker). Falha de extração (documento ilegível/corrompido) SHALL levar o caso a `FAILED` com motivo na trilha.

#### Scenario: Processamento feliz leva o caso a ANONYMIZING

- **GIVEN** um caso criado com PDFs válidos contendo o relatório padrão
- **WHEN** a task de processamento executa
- **THEN** o texto concatenado fica em `extracted_text`, o nº de ocorrência em `agency_record_number`, e o caso chega a `ANONYMIZING` com eventos `PDF_EXTRACTING` e da extração na trilha

#### Scenario: PDF corrompido leva a FAILED com motivo

- **GIVEN** um caso cujo documento não pode ser aberto pelo extrator
- **WHEN** a task executa
- **THEN** o caso transita para `FAILED` e o motivo da falha fica no payload do evento

#### Scenario: Marca d'água não vaza para o texto extraído

- **GIVEN** um relatório com a marca d'água característica (token de 5–6 dígitos, possivelmente distinto do nº de ocorrência) em ao menos 2 linhas de marca
- **WHEN** o texto é extraído
- **THEN** as linhas da marca não aparecem em `extracted_text` e o nº de ocorrência é capturado em `agency_record_number` quando presente

#### Scenario: Marca em linha solta ou com poucas repetições conta como linha de marca

- **GIVEN** um relatório cujo token de marca aparece como linha exata sozinha e/ou em linhas com poucas repetições (ex.: "48291 48291"), somando ao menos 2 linhas de marca
- **WHEN** o texto é extraído
- **THEN** essas linhas são removidas de `extracted_text` e o restante do texto clínico é preservado

#### Scenario: Marca d'água em linha solta repetida é removida

- **GIVEN** um relatório cuja marca de 5–6 dígitos aparece como linha exata sozinha (um token por linha, sem outro conteúdo), repetida ao menos 3× no documento
- **WHEN** o texto é extraído
- **THEN** essas linhas não aparecem em `extracted_text` e o restante do texto clínico é preservado

#### Scenario: Valor legítimo inline de token nomeado é preservado

- **GIVEN** um relatório com ao menos 2 linhas de marca do token "48291" (não coincidente com o nº de ocorrência) e uma linha clínica legítima citando "48291" no meio de outro conteúdo
- **WHEN** o texto é extraído
- **THEN** as linhas de banda são removidas e a ocorrência inline dentro da linha clínica permanece intacta

#### Scenario: Rastro do nº de ocorrência é varrido em qualquer lugar

- **GIVEN** um relatório cuja marca nomeada coincide com o nº de ocorrência (ao menos 2 linhas de marca) e que também cita o nº no meio de uma linha com outro conteúdo
- **WHEN** o texto é extraído
- **THEN** todas as ocorrências do nº saem de `extracted_text` e o valor vive em `agency_record_number`

#### Scenario: Linha de marca única não nomeia marca

- **GIVEN** um relatório com uma única linha composta de repetições de um token de 5–6 dígitos (ex.: valores iguais de tabela renderizados na mesma linha) e nenhuma outra linha de marca do mesmo token
- **WHEN** o texto é extraído
- **THEN** a linha permanece no `extracted_text` (nomeação exige ≥2 linhas de marca)

#### Scenario: Fronteira de página não funde linhas

- **GIVEN** um PDF multi-página cujo texto da primeira página não termina em quebra de linha e cuja segunda página começa com linha de marca
- **WHEN** o texto é extraído
- **THEN** a linha de marca da segunda página é reconhecida e removida (as páginas são concatenadas com quebra de linha)

#### Scenario: Evento de extração carrega contagens da remoção

- **GIVEN** um relatório processado com marca d'água removida
- **WHEN** o evento de extração concluída é gravado
- **THEN** o payload traz, sob `watermark`, somente contagens da remoção (a telemetria jamais carrega valores de token); o campo pré-existente `record_number` permanece com o nº quando capturado

#### Scenario: Texto sem marca permanece inalterado

- **GIVEN** um relatório sem marca d'água nomeável
- **WHEN** a limpeza executa
- **THEN** o texto entregue permanece idêntico ao extraído (sem normalização indevida)

#### Scenario: Metadados do cabeçalho populam o caso

- **GIVEN** um relatório com o cabeçalho padrão SESAB em cada página (linha de demografia com nome, `Idade: 79 a.`, sexo e raça/cor; `Dias em tela:` sozinho com `5` na linha seguinte na página 1 e `6` na última)
- **WHEN** a task de processamento executa
- **THEN** o caso fica com `patient_age`=79, `patient_gender`, `patient_race` e `days_on_screen`=6 (maior ocorrência) populados, e nenhum evento carrega esses valores

#### Scenario: Menção clínica de idade não vira metadado

- **GIVEN** um relatório cujo texto clínico contém «…Idade: 79a.» SEM os marcadores de sexo e raça/cor na mesma linha
- **WHEN** a extração de metadados executa
- **THEN** nenhum campo de metadado é populado por essa menção (a âncora exige a linha de demografia completa)

#### Scenario: Unidade de origem do cabeçalho popula o caso

- **GIVEN** um relatório com `Unid. Origem:` sozinho e o nome da unidade na linha imediatamente seguinte
- **WHEN** a task de processamento executa
- **THEN** o caso fica com `origin_unit` populado com o nome da unidade; rótulo sem valor deixa o campo vazio

#### Scenario: Cabeçalho sem metadados deixa campos vazios

- **GIVEN** um documento sem o cabeçalho padrão (texto legítimo, sem `Idade:`/`Dias em tela:`)
- **WHEN** a task de processamento executa
- **THEN** o caso é processado normalmente com os campos de metadados vazios (sem erro)

### Requirement: Gate de regulação retém documento fora do padrão

O processamento SHALL validar o documento contra o padrão do relatório de regulação (cabeçalho "RELATÓRIO DE OCORRÊNCIAS", sinais institucionais e seções operacionais, com limites configuráveis por ambiente). Documento fora do padrão SHALL deixar o caso **retido**: permanece em `PDF_EXTRACTING` com `manual_review_required=True` e motivo registrado em evento — e **nunca** avança sozinho para as etapas seguintes. Documento dentro do padrão segue normalmente.

#### Scenario: Documento fora do padrão fica retido

- **GIVEN** um caso cujo texto extraido não contém os sinais do padrão
- **WHEN** a task executa o gate
- **THEN** o caso permanece em `PDF_EXTRACTING` com `manual_review_required=True` e evento com o motivo da retenção

#### Scenario: Documento no padrão segue

- **GIVEN** um caso cujo texto contém cabeçalho e seções suficientes
- **WHEN** a task executa o gate
- **THEN** o caso avança para `ANONYMIZING` sem flag de revisão

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

O NIR SHALL ver, numa lista "meus casos", apenas os casos **ativos** (tudo exceto `CLEANED`) criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir a identificação do paciente (nome e idade, padrão da lista) num card «Identificação do paciente» com badge de status no header, além de documentos (com visualização do PDF), comunicações e a **trilha de eventos** em card collapsible «Trilha de eventos» ao final do detalhe — a mesma trilha do painel (paridade total de auditoria): cada evento com rótulo legível, badge por categoria, ator com papel em português e data/hora, card fechado por padrão; caso `FAILED` mantém o badge de erro «Falha no processamento» e o motivo técnico permanece fora da exibição. Acesso a caso criado por outro usuário SHALL ser negado. Os cards NÃO exibem o uid completo do caso — a única exceção é o sufixo curto de desambiguação (`· <8 chars>`), exibido apenas no cenário de nº duplicado descrito abaixo. Toda referência textual a um caso no âmbito do NIR — `<title>`, breadcrumb, cabeçalho do detalhe, mensagens flash e alerta de lote — SHALL usar a **referência humana**: nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel); o uid completo NÃO é exibido em texto visível (permanece apenas como chave das URLs). A linha rotulada «Nº de ocorrência» dos cards mantém `—` quando o nº está ausente (decisão vigente de `queues-remove-uid`: card sem nº fica apenas com a identificação do paciente) — o fallback em `uid <8 chars>` aplica-se a referências (títulos, breadcrumbs, cabeçalho do detalhe, mensagens), não a essa linha rotulada. Onde dois casos compartilham o mesmo nº (reenvio corrigido cria caso novo), a referência SHALL ser desambiguada com sufixo curto (`nº · <8 chars>`): link de «Reenvios corrigidos» no detalhe, página «Reenviar corrigido», flash do novo caso e cards de «meus casos» quando a lista contém nº duplicado. O detalhe exibe TODAS as rows de procedimento do caso com a origem («Declarado» NIR / «Detectado na extração») e o status de detecção após a reconciliação; no card de revisão por divergência, o resumo declarados × detectados acompanha a confirmação explícita nos novos envios e a liberação no contrato histórico.

Meus casos terá página e lista na largura do header, **sem navegação Ativos/Encerrados** (o parâmetro `?tab` legado, incluindo `closed`, cai na default segura de ativos, preservando filtros), filtros server-side preservados, contagem neutra e cards compactos em quatro regiões no desktop (4/3/3/2), sem alerta de espera ou resumo redundante. O partial e os contadores de ativos são preservados.

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
- **THEN** vê os PDFs enviados (servidos de forma segura), as comunicações do caso e, ao final, o card «Trilha de eventos» fechado por padrão; ao abrir, cada evento exibe rótulo legível, ator com papel em português e data/hora

#### Scenario: Card de meus casos identifica o paciente
- **GIVEN** um caso criado pelo NIR com nome, idade, sexo e origem extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe nome, idade (`84 a`), ocorrência e demografia disponível; sem nome exibe `—` e omite idade

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
- **GIVEN** dois casos ativos do mesmo NIR compartilhando o nº N (reenvios corrigidos)
- **WHEN** o NIR abre «meus casos»
- **THEN** os cards dos casos com nº N exibem o nº acompanhado do sufixo curto (`N · <8 chars>`); cards com nº único permanecem apenas com o nº

#### Scenario: Filtros server-side compõem por AND
- **GIVEN** casos do NIR com nºs, status e tipos distintos
- **WHEN** filtra por nº (≥3 caracteres) + status + tipo de procedimento
- **THEN** apenas os casos que atendem a todos os critérios são listados, com os filtros preservados entre página cheia e partial

#### Scenario: Caso FAILED exibe badge de erro no detalhe do NIR
- **GIVEN** um caso do próprio NIR em `FAILED`
- **WHEN** o NIR abre o detalhe
- **THEN** um badge de erro «Falha no processamento» é exibido; a trilha ao final exibe o evento de falha com rótulo legível e ator, e o motivo técnico permanece fora da exibição

#### Scenario: Trilha do NIR mostra quem fez o quê
- **GIVEN** um caso do próprio NIR com decisão médica registrada por um médico e agendamento confirmado por um agendador
- **WHEN** o NIR abre o detalhe e expande a trilha de eventos
- **THEN** os eventos exibem o nome do ator com o papel em português e a data/hora de cada ação (decisão médica, agendamento), além dos eventos de sistema com ator «Sistema»

#### Scenario: Abas Ativos e Encerrados com contadores
- **GIVEN** 2 casos ativos e 1 encerrado (`CLEANED`) do NIR
- **WHEN** abre meus casos (inclusive com `?tab=closed` legado)
- **THEN** o header exibe apenas «Meus Casos» com a contagem 2, apenas casos ativos são listados, nenhuma pill Ativos/Encerrados aparece na página e os filtros ativos são preservados

#### Scenario: Troca de aba pelo header preserva filtros
- **GIVEN** meus casos com ocorrência, status e tipo filtrados
- **WHEN** a página é aberta com `?tab=closed` legado
- **THEN** o parâmetro é ignorado (fallback seguro para ativos) e os filtros ativos são preservados na URL e na listagem

#### Scenario: Partial atualiza meus casos sem reload
- **GIVEN** meus casos abertos e um novo caso criado pelo NIR, compatível com os filtros ativos
- **WHEN** decorre o intervalo de atualização
- **THEN** o novo card aparece sem recarregar a página, preservando filtros

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
- **GIVEN** casos com andamento e falha
- **WHEN** os cards são renderizados
- **THEN** cada um mantém status e badges semânticos correspondentes

#### Scenario: Estado vazio orienta a próxima ação
- **GIVEN** uma listagem sem resultados
- **WHEN** há filtros ativos
- **THEN** o estado vazio centralizado oferece «Limpar filtros»; sem filtros, informa a ausência de casos ativos e oferece «Novo Encaminhamento»

#### Scenario: Card compacto permanece utilizável no mobile
- **GIVEN** um card com nome longo, múltiplos procedimentos e ação
- **WHEN** a lista é exibida em viewport de 390 px
- **THEN** as regiões empilham, badges quebram linha, «Ver detalhes» ocupa a largura disponível com alvo mínimo de 44 px e não há overflow horizontal

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

### Requirement: Busca de casos encerrados e detalhe histórico

O sistema SHALL oferecer ao papel ativo `nir` a página **«Casos Encerrados»** (item de navegação do NIR; URL inalterada) com busca server-side por nº de ocorrência OU nome do paciente sobre casos `CLEANED` e casos com intercorrência ativa (qualquer status) — **de qualquer criador** (escopo histórico, guarda própria fail-closed; o escopo por criador de «meus casos» permanece intacto) — com no máximo 50 resultados por consulta, estado vazio sem critério e identificação de elegibilidade para intercorrência pós-encerramento (spec `post-closure-issue`). A busca SHALL usar a referência humana nos resultados (nº de ocorrência com sufixo curto de desambiguação quando duplicado) e o uid completo NÃO aparece em texto visível. O detalhe histórico SHALL ser read-only (trilha de eventos incluída, fechada por padrão), sem ações de edição do caso, com formulário de abertura de intercorrência apenas para casos elegíveis e ação **«Reenviar corrigido»** para casos `CLEANED` de qualquer criador (levando à página de reenvio existente — spec `case-closure`). Rotas de busca e detalhe histórico são regidas pelo guard de intranet do papel `nir` (ADR-0002).

#### Scenario: Busca por nome encontra caso encerrado de outro criador

- **GIVEN** um caso `CLEANED` criado por outro usuário NIR, cujo nome do paciente contém o termo buscado
- **WHEN** o NIR busca pelo nome na página «Casos Encerrados»
- **THEN** o caso é listado com referência humana, status legível e indicação de elegibilidade, sem expor o uid completo

#### Scenario: Busca por número de ocorrência

- **GIVEN** casos encerrados com nº de ocorrência distintos, incluindo um com intercorrência ativa
- **WHEN** o NIR busca pelo nº
- **THEN** apenas os casos correspondentes são listados, incluindo o caso com intercorrência ativa (critério de inclusão), este sem ação de nova abertura

#### Scenario: Casos em andamento não elegíveis não aparecem

- **GIVEN** casos em status anteriores ao encerramento e sem intercorrência ativa
- **WHEN** o NIR busca pelo nome ou nº deles
- **THEN** nenhum deles é listado — o universo da busca é encerrados + intercorrências ativas

#### Scenario: Sem critério mantém estado vazio

- **GIVEN** a página de busca aberta sem termo
- **WHEN** renderizada
- **THEN** nenhum resultado é exibido e um estado vazio orienta o uso da busca

#### Scenario: Detalhe histórico é read-only com trilha

- **GIVEN** um resultado elegível na busca
- **WHEN** o NIR abre o detalhe histórico
- **THEN** o caso é exibido em leitura com identificação, resultado, comunicações e a trilha de eventos fechada por padrão; nenhum formulário de edição do caso é oferecido e o formulário de intercorrência aparece apenas se o caso for elegível

#### Scenario: Reenvio corrigido a partir do detalhe histórico por NIR não-criador

- **GIVEN** um caso `CLEANED` criado por outro usuário NIR, localizado na busca
- **WHEN** o NIR autenticado (não-criador) abre o detalhe histórico e aciona «Reenviar corrigido» com 1 PDF, tipo declarado e motivo
- **THEN** o novo caso é criado vinculado ao original e o reenviador é o criador do novo caso; um caso fora de `CLEANED` não oferece a ação

#### Scenario: Nav do NIR tem três itens com «Casos Encerrados»

- **GIVEN** o NIR autenticado
- **WHEN** qualquer página do intake é renderizada
- **THEN** a navegação exibe «Novo Encaminhamento», «Meus Casos» e «Casos Encerrados» (busca global) — sem item de lista de encerrados por criador; o badge de contagem de ativos aparece onde o contexto o fornece (ex.: my_cases), sem badge de encerrados

#### Scenario: Papel não NIR recebe 403

- **GIVEN** usuário com papel ativo `scheduler`
- **WHEN** acessa a busca de casos encerrados
- **THEN** recebe HTTP 403

#### Scenario: Caso fora do escopo histórico retorna 404

- **GIVEN** um identificador de caso em andamento sem intercorrência ativa
- **WHEN** o NIR acessa o detalhe histórico diretamente pela URL
- **THEN** recebe HTTP 404, sem expor qualquer conteúdo do caso
