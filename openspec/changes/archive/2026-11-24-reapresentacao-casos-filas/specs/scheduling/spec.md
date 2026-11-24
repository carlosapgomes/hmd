# Spec Delta

## MODIFIED Requirements

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

## ADDED Requirements

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
