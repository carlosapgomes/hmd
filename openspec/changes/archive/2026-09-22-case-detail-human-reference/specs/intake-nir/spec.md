# Delta: intake-nir

## MODIFIED Requirements

### Requirement: Meus casos e detalhe do NIR

O NIR SHALL ver, numa lista "meus casos", apenas os casos criados por ele, com status, tipos declarados, indicador de retenção pelo gate e a identificação do paciente (nome e idade quando presentes — o NIR é o criador do caso); o detalhe de um caso SHALL exibir a identificação do paciente (nome e idade, padrão da lista) num card «Identificação do paciente» com badge de status no header, além de documentos (com visualização do PDF) e comunicações — **sem a trilha de eventos** (a trilha vive no painel; caso `FAILED` exibe badge de erro «Falha no processamento»). Acesso a caso criado por outro usuário SHALL ser negado. Os cards NÃO exibem o uid completo do caso — a única exceção é o sufixo curto de desambiguação (`· <8 chars>`), exibido apenas no cenário de nº duplicado descrito abaixo. Toda referência textual a um caso no âmbito do NIR — `<title>`, breadcrumb, cabeçalho do detalhe, mensagens flash e alerta de lote — SHALL usar a **referência humana**: nº de ocorrência com fallback `uid <8 primeiros chars>` (padrão do painel); o uid completo NÃO é exibido em texto visível (permanece apenas como chave das URLs). A linha rotulada «Nº de ocorrência» dos cards mantém `—` quando o nº está ausente (decisão vigente de `queues-remove-uid`: card sem nº fica apenas com a identificação do paciente) — o fallback em `uid <8 chars>` aplica-se a referências (títulos, breadcrumbs, cabeçalho do detalhe, mensagens), não a essa linha rotulada. Onde dois casos compartilham o mesmo nº (reenvio corrigido cria caso novo), a referência SHALL ser desambiguada com sufixo curto (`nº · <8 chars>`): link de «Reenvios corrigidos» no detalhe, página «Reenviar corrigido», flash do novo caso e cards de «meus casos» quando a lista contém nº duplicado. O detalhe exibe TODAS as rows de procedimento do caso com a origem («Declarado» NIR / «Detectado na extração») e o status de detecção após a reconciliação; no card de revisão por divergência, o resumo declarados × detectados acompanha a ação de liberar.

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

- **GIVEN** um caso criado pelo NIR com nome e idade extraídos do cabeçalho
- **WHEN** o NIR abre meus casos
- **THEN** o card exibe o nome do paciente e a idade (`84 a`); caso sem identificação exibe `—` no lugar do nome e omite a idade

#### Scenario: Caso FAILED exibe badge de erro no detalhe do NIR

- **GIVEN** um caso do próprio NIR em `FAILED`
- **WHEN** o NIR abre o detalhe
- **THEN** um badge de erro «Falha no processamento» é exibido, sem a trilha de eventos nem o motivo técnico

#### Scenario: Cards sem o identificador interno do caso

- **GIVEN** casos com uid interno e nº de ocorrência
- **WHEN** a lista é renderizada
- **THEN** nenhum card exibe o uid completo do caso — o nº de ocorrência (com `—` quando ausente) e a identificação do paciente são os identificadores do card, sem fragmento de uid em cards com nº único (o sufixo curto só surge no cenário de nº duplicado)

#### Scenario: Detalhe retido por divergência lista declarados e detectados

- **GIVEN** um caso do próprio NIR retido em `LLM_EXTRACTING` por divergência (`angio_art_perif` declarada não-detectada e `art_perif` detectada não-declarada)
- **WHEN** o NIR abre o detalhe
- **THEN** a seção de procedimentos exibe ambas as rows com os rótulos legíveis e os badges de origem e detecção («Não detectado», «Detectado na extração»), e o card de revisão resume declarados × detectados junto ao botão de liberação

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
- **WHEN** o NIR conclui criação, confirmação de recebimento, liberação de gate ou reenvio de documentos
- **THEN** a mensagem flash cita a referência humana (nº de ocorrência; fallback `uid <8 chars>` quando ausente), nunca o uid completo

#### Scenario: Referência do reenvio corrigido é desambiguada

- **GIVEN** um caso encerrado com nº N e um reenvio corrigido criado (novo caso com o mesmo nº N)
- **WHEN** o NIR vê o flash de reenvio criado, a página «Reenviar corrigido» do original, e o link do novo caso na lista «Reenvios corrigidos» do detalhe
- **THEN** a referência exibida é `N · <8 primeiros chars do uid>` para distinguir original e novo caso

#### Scenario: Meus casos com nº duplicado exibe sufixo curto

- **GIVEN** dois casos do mesmo NIR compartilhando o nº N (original encerrado + reenvio corrigido)
- **WHEN** o NIR abre «meus casos»
- **THEN** os cards dos casos com nº N exibem o nº acompanhado do sufixo curto (`N · <8 chars>`); cards com nº único permanecem apenas com o nº
