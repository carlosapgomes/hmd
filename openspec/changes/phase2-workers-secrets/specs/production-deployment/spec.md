# production-deployment Specification (delta)

## ADDED Requirements

### Requirement: Workers do pipeline com segredo por arquivo e limites de recurso

Os workers do pipeline SHALL receber a chave da OpenRouter exclusivamente por
arquivo de segredo (montagem read-only do secret `openrouter_api_key`), com
suporte a `OPENROUTER_API_KEY_FILE` nas settings com precedência sobre a
variável de ambiente plana e comportamento fail-closed para arquivo ilegível
ou vazio. Apenas os serviços com egress de saída (`worker-llm` e
`worker-attachments`) SHALL receber a chave e as variáveis de modelo
(`LLM1_MODEL`, `LLM2_MODEL`, `VISION_MODEL`); serviços sem egress SHALL
permanecer sem qualquer variável da OpenRouter. Cada worker SHALL declarar
limite de memória ajustável por variável de ambiente no host. A ativação do
intake SHALL permanecer controlada por `INTAKE_ENABLED` (default `false`),
de modo que a configuração dos workers não ative o pipeline por si só.

#### Scenario: Chave OpenRouter só por arquivo nos workers com egress

- **GIVEN** o compose de produção resolvido com todos os profiles
- **WHEN** os serviços `worker-llm` e `worker-attachments` são inspecionados
- **THEN** ambos montam o secret `openrouter_api_key` e declaram
  `OPENROUTER_API_KEY_FILE` apontando para a montagem, sem a chave em
  variável de ambiente plana

#### Scenario: Serviços sem egress não recebem a chave

- **GIVEN** o compose de produção resolvido com todos os profiles
- **WHEN** os serviços `web`, `worker-pdf`, `worker-anonymization` e
  `migrate` são inspecionados
- **THEN** nenhum deles declara `OPENROUTER_API_KEY`,
  `OPENROUTER_API_KEY_FILE` ou variáveis de modelo

#### Scenario: Arquivo de chave tem precedência e falha fechado

- **GIVEN** as settings de produção com `OPENROUTER_API_KEY_FILE` apontando
  para um arquivo legível e não vazio
- **WHEN** as settings são carregadas
- **THEN** `OPENROUTER_API_KEY` vem do arquivo; se o arquivo estiver vazio ou
  ilegível, o carregamento falha com `ImproperlyConfigured` (nunca cai
  silenciosamente para a env plana)

#### Scenario: Cada worker com limite de memória ajustável

- **GIVEN** o compose de produção resolvido com o profile `workers`
- **WHEN** os 4 workers são inspecionados
- **THEN** cada um declara `deploy.resources.limits.memory` com valor
  passável por variável de ambiente e default não vazio

#### Scenario: Configurar workers não ativa o intake

- **GIVEN** o compose de produção com o secret e os modelos configurados no
  host, sem `INTAKE_ENABLED` definida
- **WHEN** o stack sobe com o profile `workers`
- **THEN** o envio de relatórios permanece desligado até
  `INTAKE_ENABLED=true` no serviço `web`

#### Scenario: Rollback da fase 2 por variável de ambiente

- **GIVEN** a fase 2 ativa (intake ligado e workers rodando)
- **WHEN** `INTAKE_ENABLED` volta a `false` e o profile `workers` é
  desativado
- **THEN** o serviço `web` volta a recusar envios e os casos já criados
  permanecem íntegros com seus estados
