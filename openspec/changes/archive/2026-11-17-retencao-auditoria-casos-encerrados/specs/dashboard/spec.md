# Spec Delta

## ADDED Requirements

### Requirement: Sumário apresentado ao médico no detalhe do painel

O detalhe do caso no painel SHALL exibir, para `manager`/`admin`, o
conteúdo consultivo que foi apresentado ao médico na avaliação — o sumário
clínico e a estrutura extraída re-identificados (mesma fonte e mesma forma
de apresentação do detalhe médico, por seção, nunca JSON bruto) — em card
collapsible fechado por padrão. O card SHALL cobrir casos em qualquer
fase do fluxo, inclusive casos encerrados cujos dados foram retidos pela
política temporária; quando o caso não possui sumário/estrutura (ainda não
passou pelo pipeline, falhou antes da sumarização ou os dados foram
purgados/minimizados), o card SHALL exibir mensagem explícita de
indisponibilidade em vez de omitir-se — de forma independente por bloco
(um caso com estrutura mas sem sumário exibe a estrutura e placeholder
apenas no sumário). O acesso permanece sob o mesmo
guard do detalhe (`manager`/`admin`; outros papéis ativos → 403) e nenhum
token de pseudônimo SHALL vazar na renderização.

#### Scenario: Card collapsible com sumário e estrutura

- **GIVEN** um caso que passou pela sumarização, com artefatos do pipeline e mapa de pseudônimos
- **WHEN** o manager abre o detalhe do caso no painel
- **THEN** aparece card fechado por padrão com o sumário clínico e a estrutura extraída; ao abrir, as linhas do sumário e as seções da estrutura aparecem re-identificadas (nomes/valores reais, sem tokens de pseudônimo), na mesma forma de apresentação do detalhe médico

#### Scenario: Caso encerrado com dados retidos

- **GIVEN** um caso `CLEANED` encerrado em modo de retenção, com artefatos preservados
- **WHEN** o manager abre o detalhe no painel
- **THEN** o card exibe o mesmo sumário e a mesma estrutura apresentados ao médico na avaliação

#### Scenario: Sem artefatos exibe indisponibilidade

- **GIVEN** um caso que ainda não passou pelo pipeline, que falhou antes da sumarização, ou cujos dados foram purgados/minimizados
- **WHEN** o detalhe é aberto no painel
- **THEN** o card permanece presente exibindo mensagem explícita de indisponibilidade no bloco sem artefato — de forma independente por bloco (caso com estrutura mas sem sumário exibe a estrutura e o placeholder apenas no sumário)

#### Scenario: Papel fora de manager/admin não vê o sumário

- **GIVEN** um usuário com papel ativo `nir`, `doctor` ou `scheduler`
- **WHEN** acessa a rota do detalhe do caso no painel
- **THEN** recebe 403 e nenhum conteúdo do sumário é entregue
