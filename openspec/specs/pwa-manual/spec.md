# pwa-manual Specification

## Purpose

Aplicação instalável como PWA com identidade HMD (ícone com as letras HMD) e manual de usuário por papel acessível na navbar.

## Requirements

### Requirement: PWA instalável com identidade HMD

O sistema SHALL servir um manifest web app com nome e short_name "HMD", ícones base e maskable (SVG e PNGs) exibindo as letras HMD, e um service worker registrado no layout base que faz cache de estáticos com estratégia network-first e nunca intercepta requisições de não-GET nem rotas de PDF do caso.

#### Scenario: Manifest e ícones HMD servidos

- **GIVEN** o app publicado
- **WHEN** o manifest e os ícones são requisitados
- **THEN** o manifest declara nome/short_name HMD e aponta para ícones existentes com as letras HMD

#### Scenario: Service worker registrado e conservador

- **GIVEN** uma página autenticada renderizada
- **WHEN** o navegador processa o layout
- **THEN** o service worker é registrado e sua lógica ignora requisições POST e rotas de PDF

#### Scenario: Layout declara PWA

- **GIVEN** qualquer página do app
- **WHEN** o HTML é renderizado
- **THEN** inclui o link do manifest e a meta theme-color

### Requirement: Manual de usuário por papel

O sistema SHALL publicar um manual de usuário em HTML descrevendo o ciclo do caso e as operações de cada papel (NIR, médico, agendador) e dos recursos transversais (notificações, painel, instalação do app), acessível por link na navbar em nova aba.

#### Scenario: Manual acessível pela navbar

- **GIVEN** um usuário autenticado
- **WHEN** abre o manual pelo link da navbar
- **THEN** vê a visão geral do ciclo e as seções por papel

#### Scenario: Manual cobre o ciclo completo

- **GIVEN** o manual aberto
- **WHEN** as seções são lidas
- **THEN** descreve envio com anexos, decisão médica, agendamento por unidade, resposta final e ciência do NIR
