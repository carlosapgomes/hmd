# case-closure Specification (delta)

## MODIFIED Requirements

### Requirement: Resultado e casos encerrados visíveis ao criador

O detalhe do caso para o NIR SHALL exibir, a partir da decisão médica, o
resultado (decisões por procedimento com motivos, dados de agendamento e a
resposta final em destaque) e um meio de confirmar o recebimento apenas
enquanto o caso estiver em `FINAL_REPLY_POSTED` e for de sua autoria. A
lista "meus casos" SHALL separar ativos de encerrados — e casos encerrados
ADMINISTRATIVAMENTE SHALL aparecer na aba de encerrados com o resultado
"Encerrado administrativamente" e o motivo registrado.

#### Scenario: Resultado exibido ao criador

- **GIVEN** um caso com resposta final publicada, criado pelo NIR autenticado
- **WHEN** ele abre o detalhe do caso
- **THEN** decisões com motivos, dados de agendamento e a resposta final são exibidos, junto ao botão de confirmação de recebimento

#### Scenario: Ciência apenas do criador no estado certo

- **GIVEN** um caso em `FINAL_REPLY_POSTED` criado por outro NIR e um caso do NIR autenticado já encerrado
- **WHEN** ele tenta confirmar recebimento de qualquer um deles
- **THEN** recebe 404 no caso alheio e mensagem de erro no caso já encerrado, sem mudança de estado

#### Scenario: Aba de encerrados lista apenas casos CLEANED

- **GIVEN** casos do NIR em estados variados, incluindo encerrados
- **WHEN** ele abre a aba de encerrados em "meus casos"
- **THEN** apenas os casos `CLEANED` de sua autoria são listados

#### Scenario: Encerramento administrativo visível ao criador

- **GIVEN** um caso do criador encerrado administrativamente
- **WHEN** o criador abre a aba de encerrados em "meus casos"
- **THEN** o caso aparece com o resultado "Encerrado administrativamente" e
  o motivo registrado

## ADDED Requirements

### Requirement: Encerramento administrativo do caso

O sistema SHALL oferecer transição excepcional de qualquer status diferente
de `CLEANED` para `CLEANED` (encerramento administrativo), restrita às
interfaces do painel (`manager`/`admin`), exigindo código de motivo do
catálogo fixo (`processing_error`, `llm_failure`, `system_bug`,
`stuck_lock`, `duplicate_reprocess`, `other`) e texto descritivo
obrigatório. O encerramento SHALL registrar evento de auditoria
`CASE_ADMINISTRATIVELY_CLOSED` com código, texto, autor e papel; SHALL
limpar o lock operacional persistido do caso; SHALL minimizar os dados
clínicos do caso (documentos e anexos removidos, campos clínicos zerados —
mesma limpeza do encerramento por ciência); SHALL notificar o criador do
caso; e SHALL ser recusado enquanto houver lock de worker com lease válida.
Casos já em `CLEANED` SHALL ser rejeitados.

#### Scenario: Supervisor encerra caso travado com motivo

- **GIVEN** um caso em `FAILED` e um usuário com papel ativo `manager`
- **WHEN** encerra administrativamente com código `processing_error` e texto
- **THEN** o caso vai a `CLEANED`, o evento de auditoria é registrado com o
  payload completo e o lock do caso fica limpo

#### Scenario: Encerramento minimiza dados clínicos

- **GIVEN** um caso com documentos, anexos e campos clínicos preenchidos
- **WHEN** é encerrado administrativamente
- **THEN** documentos e anexos são removidos, os campos clínicos ficam
  zerados e o download de documento do caso não é mais possível

#### Scenario: Encerramento recusado durante processamento ativo

- **GIVEN** um caso com lock de worker e lease ainda válida
- **WHEN** tenta-se o encerramento administrativo
- **THEN** a operação é recusada com erro e o caso permanece inalterado

#### Scenario: Encerramento exige texto do motivo

- **GIVEN** um caso ativo e o formulário de encerramento sem texto
- **WHEN** o encerramento é submetido
- **THEN** é rejeitado com erro de validação e o caso permanece no estado
  anterior

#### Scenario: Motivo fora do catálogo é rejeitado

- **GIVEN** o formulário de encerramento com código inexistente no catálogo
- **WHEN** o encerramento é submetido
- **THEN** é rejeitado com erro de validação

#### Scenario: Caso já concluído não é encerrável novamente

- **GIVEN** um caso já em `CLEANED`
- **WHEN** tenta-se o encerramento administrativo
- **THEN** a operação é rejeitada com erro

#### Scenario: Criador é notificado do encerramento

- **GIVEN** um caso encerrado administrativamente
- **WHEN** o criador consulta suas notificações
- **THEN** existe notificação apontando para o caso com o marco de
  encerramento administrativo

#### Scenario: Papel fora de manager/admin não encerra

- **GIVEN** um usuário com papel ativo `nir`, `doctor` ou `scheduler`
- **WHEN** tenta submeter o encerramento administrativo
- **THEN** recebe 403
