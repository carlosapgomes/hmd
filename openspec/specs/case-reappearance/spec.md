# case-reappearance Specification

## Purpose

Reconhecer e resolver reapresentações do mesmo pedido, preservando espera, decisões, documentos e rastreabilidade sem criar triagem clínica adicional.

## Requirements

### Requirement: Identidade explicável do mesmo pedido

O sistema SHALL exigir nome normalizado e conjunto efetivo de procedimentos iguais, mais regulação, CNS válido do paciente ou origem iguais e não vazios. Regulação/CNS dão evidência forte; origem exige conferência. Conflitos impedem automação. A multiplicidade SHALL considerar fortes, fracos e conflitantes nas filas médica e de agendamento; mais de um plausível impede seleção automática.

#### Scenario: Nova regulação com CNS igual
- **WHEN** chega pedido recente com nome e procedimento iguais e CNS válido igual ao caso na fila, mas regulação diferente
- **THEN** o par é candidato forte, sem exigir igualdade da regulação

#### Scenario: Mesmo paciente com procedimento diferente
- **WHEN** os identificadores coincidem mas o conjunto efetivo dos procedimentos difere
- **THEN** os pedidos permanecem independentes, sem encerramento por reapresentação

#### Scenario: Nome e origem sem identificador forte
- **WHEN** nome, procedimento e origem coincidem sem CNS ou regulação iguais
- **THEN** o sistema solicita conferência NIR, nunca encerra automaticamente

#### Scenario: Vazio e homônimo não comprovam identidade
- **WHEN** apenas nome coincide e as demais evidências estão vazias ou diferentes
- **THEN** não é criada resolução automática

#### Scenario: CNS conflitante ou múltiplos candidatos
- **WHEN** regulação coincide mas CNS válidos divergem, ou existem múltiplos candidatos ativos plausíveis
- **THEN** nenhuma seleção/encerramento automático ocorre e a ambiguidade é apresentada à conferência

### Requirement: Idade auxiliar sem obrigatoriedade

O sistema SHALL aceitar diferença de idade de até um ano. Na evidência somente nome+origem, diferença maior exclui o candidato; idade ausente não impede conferência. Na evidência forte, diferença maior exige conferência em vez de encerramento automático. Idade zero SHALL ser tratada como informada.

#### Scenario: Tolerância e ausência
- **WHEN** nome+origem coincide e as idades diferem um ano ou uma delas está ausente
- **THEN** o candidato pode ser conferido pelo NIR

#### Scenario: Idades incompatíveis
- **WHEN** ambas as idades diferem dois anos
- **THEN** candidato somente por origem é descartado e candidato forte exige conferência

### Requirement: CNS estrutural e privado

O sistema SHALL usar somente CNS válido de bloco textual SESAB corroborado: título do relatório, identificação, assinatura institucional e slot CNS seguido de Dias em tela, antes de Nome Social. Demografia isolada e narrativa não SHALL abrir bloco. Formato não reconhecido, ausência, invalidade ou ambiguidade deixam identidade CNS vazia sem bloquear o caso. CNS não SHALL sair em claro para terceiros, eventos ou logs.

#### Scenario: Bloco textual completo após extração do PDF
- **WHEN** texto extraído/limpo tem título inteiro, Paciente e Código antes da assinatura, sequência Central Estadual de Regulação → CNS → Dias em tela e término Nome Social antes de qualquer seção clínica
- **THEN** somente CNS desse slot pode fornecer identidade, sem usar coordenadas ou proximidade arbitrária

#### Scenario: Demografia na narrativa não reinicia cabeçalho
- **WHEN** narrativa contém nome, idade, sexo, raça/cor e CNS, ou campos Nome/Paciente/Código soltos, sem bloco completo
- **THEN** nenhum deles abre ou reabre bloco e CNS narrativo não identifica nem contamina identidade legítima

#### Scenario: Formato parcial não autoriza busca livre
- **WHEN** falta título, identificação corroborante, sequência institucional ou término Nome Social, ou há narrativa antes desse término
- **THEN** candidato é ignorado, sem procurar CNS adiante, sem bloquear processamento nem alterar os demais critérios de identidade

#### Scenario: Cabeçalhos repetidos e rótulo vazio
- **WHEN** mesmo CNS válido se repete entre páginas, ou rótulo CNS está vazio seguido de outro campo
- **THEN** repetições idênticas deduplicam e rótulo vazio não captura a linha seguinte; valores válidos distintos ou valor não vazio inválido deixam CNS de identidade vazio

#### Scenario: CNS narrativo não identifica paciente
- **WHEN** existe CNS válido em narrativa mas não no campo inequívoco do paciente
- **THEN** ele pode ser mascarado pela anonimização, mas não serve como CNS de identidade

#### Scenario: Documento substituído limpa CNS antigo
- **WHEN** o documento é substituído pelo gate e o novo não informa CNS
- **THEN** o CNS anterior não permanece como evidência de identidade

### Requirement: Conferência NIR com duas respostas

Qualquer NIR da equipe SHALL conferir candidatos no detalhe do novo, comparando identificação, procedimento, origem, recebimento, situação e ambos os PDFs disponíveis. As únicas respostas SHALL ser mesmo pedido ou pedido independente. Fechar sem responder mantém a suspeita sem bloquear processamento nem encerrar casos. Respostas SHALL ser auditadas por operador e revisão vigente.

#### Scenario: Confirmar mesmo pedido
- **WHEN** um NIR não-criador confirma o mesmo pedido em comparação vigente
- **THEN** o vínculo é confirmado e a resolução segue o estado atual do original, com autoria real registrada

#### Scenario: Pedido independente
- **WHEN** o NIR responde pedido independente
- **THEN** os dois seguem seus fluxos e o mesmo par/revisão não volta a ser perguntado

#### Scenario: Fechar não significa responder
- **WHEN** o NIR fecha o detalhe sem escolher uma das duas ações
- **THEN** não há consentimento implícito, terceiro botão de resposta, encerramento ou bloqueio do pipeline

#### Scenario: Fonte ou procedimento mudou
- **WHEN** é enviada confirmação de comparação cuja fonte ou declaração foi alterada
- **THEN** a resposta é recusada sem resolução e o usuário é orientado a reabrir

#### Scenario: POST indevido
- **WHEN** papel não NIR, escolha múltipla/inválida ou par fora do escopo é enviado
- **THEN** nenhuma confirmação nem encerramento é persistido

### Requirement: Substituição médica somente com novo pronto

O sistema SHALL substituir caso aguardando médico apenas quando o novo estiver integralmente processado para o mesmo pedido e a identidade for forte ou confirmada. Original é encerrado e novo mantém o marco mais antigo da espera acumulada, sem modificar sua criação. Falha ou retenção do novo não SHALL retirar o original da fila.

#### Scenario: Novo pronto substitui original
- **WHEN** novo conclui análise válida e original ainda aguarda médico sem reserva ativa
- **THEN** original encerra vinculado, novo permanece decidível e exibe espera herdada inclusive após reenvios sucessivos

#### Scenario: Novo falha ou fica retido
- **WHEN** processamento novo falha ou aguarda revisão de procedimento
- **THEN** original continua disponível e sem encerramento por esse reenvio

#### Scenario: Médico está trabalhando
- **WHEN** original tem reserva viva ou é decidido durante o processamento novo
- **THEN** a reserva não é tomada e o efeito é adiado/revalidado, sem sobrescrever decisão

### Requirement: Reapresentação não interrompe agendamento

O sistema SHALL manter original aguardando agendamento e sua aprovação, encerrar novo elegível vinculado e contar cada envio resolvido uma única vez. Não SHALL perguntar sobre atualização clínica, exigir retorno ao NIR, solicitar revisão médica ou suspender agendamento por reapresentação. Relatórios e anexos recebidos SHALL permanecer separados dos que fundamentaram a aprovação.

#### Scenario: Reenvio enquanto aguarda agendamento
- **WHEN** reapresentação forte ou confirmada é resolvida contra original ainda na fila de agendamento
- **THEN** apenas novo encerra, original mantém estado, decisões e reserva, contador aumenta uma vez e documentos novos ficam vinculados

#### Scenario: Reconciliação antes de arquivar reenvio
- **WHEN** original aguarda cateterismo e novo foi declarado cateterismo mas LLM1 detecta outro procedimento
- **THEN** novo não encerra antes da reconciliação nem ignora divergência; original segue agendamento, novo mantém gate existente e não executa LLM2 enquanto retido

#### Scenario: Relatório mudou clinicamente
- **WHEN** novo relatório contém diferenças clínicas
- **THEN** a reapresentação não cria triagem, suspensão nem reavaliação automática; risco de descoberta posterior é aceito

#### Scenario: Original já foi agendado
- **WHEN** original já saiu da fila antes da resolução
- **THEN** a regra de reapresentação em espera não é aplicada com estado antigo nem cancela a marcação

### Requirement: Resolução auditada e idempotente

O sistema SHALL revalidar identidade, procedimento, estado e reservas na aplicação atômica do vínculo e encerramento. Retry ou concorrência não SHALL duplicar contagem nem encerrar todos os representantes do pedido. Encerramento por reapresentação SHALL ser distinto de decisão clínica e de encerramento administrativo.

#### Scenario: Retentativa esgotada preserva trabalho
- **WHEN** reserva viva impede aplicação até atingir limite técnico de recuperação
- **THEN** pendência permanece auditada como dependente de suporte, sem loop infinito, encerramento, tomada de reserva ou terceira resposta NIR

#### Scenario: Dois reenvios e retry
- **WHEN** dois reenvios concorrentes do mesmo pedido são resolvidos e um job é repetido
- **THEN** resta um representante operacional por resolução válida, sem contagem dupla, ciclos ou vínculo aplicado a dois originais

#### Scenario: Novo já recebeu decisão médica
- **WHEN** confirmação chega depois de uma decisão médica já registrada no novo
- **THEN** a conferência pode ficar registrada como contexto, mas não desfaz a decisão nem encerra automaticamente esse caso; a interface informa que os casos já evoluíram

#### Scenario: Evento e métricas verdadeiros
- **WHEN** um caso encerra por reapresentação
- **THEN** ambos os casos apresentam eventos correlacionados e não são criadas decisão médica, decisão de agendamento ou conclusão clínica fictícias

#### Scenario: Forte coexistindo com outro plausível
- **WHEN** existe um forte médico e outro candidato fraco ou conflitante, médico ou aguardando agendamento
- **THEN** não ocorre seleção ou encerramento automático; a falta de resposta NIR não bloqueia o processamento normal

#### Scenario: Reentrada com vínculo pendente
- **WHEN** o mesmo par, revisão e kind são resolvidos novamente enquanto há vínculo pending
- **THEN** o vínculo é reutilizado sem IntegrityError, agenda/eventos duplicados ou falha espúria do pipeline; aplicação posterior usa esse mesmo vínculo quando elegível

### Requirement: Exclusão mútua com participantes frescos

Finalização, resolução e retry SHALL adquirir mutex estável/versionado antes de row locks do grupo ordenado e revalidar chave, participantes, identidade, autoridade, completude, estado e reservas antes de qualquer efeito. Deriva SHALL abortar sem escrita parcial e reiniciar em nova transação, sem expandir o grupo ou adquirir outro mutex depois de row locks. Não SHALL executar rede, deleção física ou nova análise LLM sob esses locks.

#### Scenario: Nome ou participantes mudam durante aquisição
- **WHEN** o nome travado ou o conjunto fresco de participantes difere daquele usado para adquirir mutex e coletar o grupo
- **THEN** não há substituição, evento de aplicação ou liberação de lease parcial; a operação retoma desde a leitura sem lock em nova transação

#### Scenario: Limite de retomadas por deriva no pipeline
- **WHEN** a finalização encontra deriva em todas as três tentativas transacionais totais da chamada
- **THEN** aborta sem substituição, sinaliza erro técnico explícito e encaminha o incoming ainda em processamento ao tratamento existente de FAILED sob seus guards; preserva original/decisões concorrentes, libera somente a própria lease pelo fluxo existente e não repete LLM nem inicia recuperação automática desse erro

#### Scenario: Limite de retomadas em caso já pronto
- **WHEN** resolução ou retry de caso pronto esgota as três tentativas transacionais totais por deriva
- **THEN** preserva o estado existente e sinaliza erro ao chamador, sem aplicação fictícia, novo estado FSM ou conversão automática de caso decidível em FAILED

#### Scenario: Decisão médica vence a corrida
- **WHEN** o médico decide o original antes da revalidação da resolução concorrente
- **THEN** sua decisão não é sobrescrita e a regra usa o estado fresco, sem forçar reserva ou encerramento

#### Scenario: Criação empatada
- **WHEN** dois casos têm created_at exatamente igual
- **THEN** somente o de case_id menor é anterior ao outro; ambos não se consideram anteriores mutuamente

### Requirement: Recuperação limitada à revisão original

Retry SHALL permanecer ligado ao par e digest registrados. Mudança de revisão SHALL tornar a tentativa antiga inelegível sem reescrever digest ou reiniciar orçamento. Nome/dedupe de agenda SHALL distinguir vínculo, revisão e tentativa; first_attempt_at não SHALL reiniciar nem attempt_count diminuir. Limite de 24 tentativas ou 24h SHALL impedir retomada automática inclusive por entrega duplicada; nenhum retry SHALL disparar do release de reserva.

#### Scenario: Documento ou identidade mudou
- **WHEN** um retry encontra revisão corrente diferente do digest registrado
- **THEN** o vínculo antigo permanece auditável e inelegível, sem aplicar, alterar seu par/digest ou reiniciar seus limites; não há reanálise LLM automática

#### Scenario: Job antigo após esgotamento
- **WHEN** job atrasado ou duplicado chega após retry_exhausted, window_exceeded ou state_ineligible, mesmo depois da liberação da lease
- **THEN** ele é no-op sem encerramento, novo orçamento, agenda ou evento duplicado

#### Scenario: Revisões diferentes não compartilham agenda
- **WHEN** duas revisões são comparadas para deduplicar uma tentativa
- **THEN** agenda de revisão diferente não é tratada como recuperação válida da revisão atual

### Requirement: Pendência operacional visível

Desde S2, eventos de falha de agenda, esgotamento e reenqueue SHALL ficar somente na trilha do incoming_case, com aviso atual nos detalhes NIR e gerencial autorizados desse incoming. Não SHALL haver PII em eventos/logs, novo painel ou terceira resposta NIR. Agenda inexistente não SHALL ser anunciada como programada: timestamp não é dispatch. GET não SHALL escrever.

#### Scenario: Falha real de banco ao agendar
- **WHEN** a gravação de uma agenda falha no adiamento inicial ou no retry vencido
- **THEN** a transação é tratada sem sucesso falso, a pendência sem Schedule é identificada na cronologia/leitura e há orientação de suporte; se nem auditoria/recuperação puder ser persistida, o retorno sinaliza falha e log sanitizado

#### Scenario: Pendência não duplica aviso no prior
- **WHEN** um vínculo médico fica pendente, falha ao agendar, esgota ou é reenfileirado
- **THEN** os eventos de pendência e seu aviso ficam no incoming, não no prior; uma substituição efetiva continua emitindo seus dois eventos correlacionados, um em cada caso

#### Scenario: Pendência regularizada
- **WHEN** recuperação passa a estar realmente agendada ou o vínculo é aplicado
- **THEN** o detalhe distingue o estado atual da falha histórica, preservando os eventos sem continuar anunciando pendência antiga como atual

### Requirement: Recuperação técnica explícita e limitada

Recuperação técnica SHALL permitir simulação e reenqueue explícito, idempotente e auditado, apenas para pendência não terminal elegível. Não SHALL executar resolução ou LLM diretamente, tomar leases, alterar revisão ou reiniciar orçamento. Simulação não SHALL escrever; execução respeita flag e limites originais e só anuncia agenda realmente persistida.

#### Scenario: Recuperação técnica idempotente
- **WHEN** operador simula e depois solicita reenqueue de vínculo não terminal elegível sem agenda, dentro dos limites originais
- **THEN** simulação não escreve e execução agenda somente o efeito no cluster llm com auditoria; repetição com agenda válida não duplica tentativa ou evento

#### Scenario: Recuperação indevida recusada
- **WHEN** operador tenta reenqueue com flag off, revisão obsoleta, vínculo aplicado/inelegível/esgotado ou limite atingido
- **THEN** nenhuma resolução ou reativação ocorre, o resultado explica a recusa e nenhum orçamento ou revisão é reiniciado

#### Scenario: Saída de simulação e sucesso real
- **WHEN** o comando simula um vínculo existente com argumentos válidos, agenda efetivamente ou encontra agenda válida já existente
- **THEN** retorna código 0 distinguindo SIMULAÇÃO, AGENDADO e NO-OP; simulação de inelegibilidade é somente diagnóstico, não autorização de escrita

#### Scenario: Saída de recusa ou erro
- **WHEN** --apply é recusado por inelegibilidade, o vínculo não existe ou a persistência falha
- **THEN** retorna código 1 e motivo sanitizado, sem anunciar recuperação agendada

#### Scenario: Uso inválido do comando
- **WHEN** os argumentos ou UUID são malformados
- **THEN** retorna código 2 sem executar recuperação
