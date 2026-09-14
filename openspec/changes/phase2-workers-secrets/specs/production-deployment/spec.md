# production-deployment Specification (delta)

## ADDED Requirements

### Requirement: Workers do pipeline com segredo por arquivo e limites de recurso

Os workers do pipeline SHALL receber a chave da OpenRouter exclusivamente por
arquivo de segredo (montagem read-only do secret `openrouter_api_key`), com
suporte a `OPENROUTER_API_KEY_FILE` nas settings com precedência sobre a
variável de ambiente plana e comportamento fail-closed para arquivo ilegível
ou vazio. Apenas os processos que chamam a OpenRouter (`worker-llm` e
`worker-attachments`) SHALL receber a chave e as variáveis de modelo que
consomem (`worker-llm`: `LLM1_MODEL` e `LLM2_MODEL`; `worker-attachments`:
`LLM1_MODEL` e `VISION_MODEL`); os demais serviços — inclusive o `web`, que
usa a rede de egress apenas para AD/DNS/Kerberos — SHALL permanecer sem
qualquer variável da OpenRouter. O `worker-anonymization` SHALL permitir
trocar o modelo spaCy (`ANONYMIZATION_SPACY_MODEL`) por variável de ambiente,
sem receber a chave. Cada worker SHALL declarar limite de memória ajustável
por variável de ambiente no host, orçado pelo número de processos do cluster. A ativação do
intake SHALL permanecer controlada por `INTAKE_ENABLED` (default `false`),
de modo que a configuração dos workers não ative o pipeline por si só.

#### Scenario: Chave OpenRouter só por arquivo nos workers com egress

- **GIVEN** o compose de produção resolvido com todos os profiles
- **WHEN** os serviços `worker-llm` e `worker-attachments` são inspecionados
- **THEN** ambos montam o secret `openrouter_api_key` e declaram
  `OPENROUTER_API_KEY_FILE` apontando para a montagem, sem a chave em
  variável de ambiente plana

#### Scenario: Serviços que não chamam a OpenRouter não recebem a chave

- **GIVEN** o compose de produção resolvido com todos os profiles
- **WHEN** os serviços `web`, `worker-pdf`, `worker-anonymization` e
  `migrate` são inspecionados
- **THEN** nenhum deles declara `OPENROUTER_API_KEY`,
  `OPENROUTER_API_KEY_FILE` ou variáveis de modelo — inclusive o `web`, que
  partilha a rede de egress apenas para AD/DNS/Kerberos

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
- **THEN** cada um declara `mem_limit` com valor passável por variável de
  ambiente e default não vazio, orçado pelo número de processos do cluster
  (o `worker-anonymization` cobre as duas instâncias do engine de
  anonimização)

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

#### Scenario: Modelo spaCy do worker de anonimização é ajustável por env

- **GIVEN** o compose de produção resolvido com o profile `workers`
- **WHEN** o serviço `worker-anonymization` é inspecionado
- **THEN** ele declara `ANONYMIZATION_SPACY_MODEL` com default
  `pt_core_news_lg` e sem nenhuma variável da OpenRouter
