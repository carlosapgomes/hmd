# ui-shell Specification (delta)

## Purpose

Vocabulário de abas do shell — «Meus Casos» passa a «Casos em Processamento» (escopo de equipe NIR; desvio deliberado do vocabulário ats-web, decisão do dono 2026-11-22).

## MODIFIED Requirements

### Requirement: Fundação visual e interações transversais

O sistema SHALL renderizar texto e títulos nas fontes do tema
hospitalar, com tokens de cor, componentes (botões, badges, status) e
controles de ação com altura mínima de 44px — primária HMD preservada.
Cada sino SHALL refletir uma nova notificação em até 60 segundos sem
recarregar a página (polling de 45 s com backoff até 5 min e pausa em
aba oculta, mesmo padrão do ats-web).

#### Scenario: Sino atualiza sem navegação

- **GIVEN** página autenticada aberta e uma nova notificação criada
- **WHEN** decorrem até 60 segundos
- **THEN** a contagem do sino reflete o novo total sem reload

#### Scenario: Abas usam os termos do ats-web

- **GIVEN** usuário navegando nas filas do NIR, médico e agendador
- **WHEN** as abas são exibidas
- **THEN** os rótulos seguem o vocabulário do ats-web (`Pendentes`,
  `Decididos`, `Novo Encaminhamento`, `Casos em Processamento`,
  `Casos Encerrados`) — sendo `Casos em Processamento` um desvio
  deliberado do vocabulário ats-web (escopo de equipe NIR, decisão do dono
  2026-11-22)
