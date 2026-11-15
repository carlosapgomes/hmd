# Delta: pwa-manual

## MODIFIED Requirements

### Requirement: PWA instalável com identidade HMD

O sistema SHALL servir um manifest web app com nome `SAR – HMD` e
short_name `HMD`, ícones base e maskable (SVG e PNGs) exibindo a sigla
HMD branca centralizada sobre o fundo plano da cor primária, com
variante de contraste (fundo claro, sigla na primária) para superfícies
escuras, e um service worker registrado no layout base que faz cache de
estáticos com estratégia network-first e nunca intercepta requisições
de não-GET nem rotas de PDF do caso.

#### Scenario: Manifest e ícones HMD servidos

- **GIVEN** o app publicado
- **WHEN** o manifest e os ícones são requisitados
- **THEN** o manifest declara nome `SAR – HMD`/short_name `HMD` e
  aponta para ícones existentes com a sigla HMD, incluindo a variante
  de contraste

#### Scenario: Service worker registrado e conservador

- **GIVEN** uma página autenticada renderizada
- **WHEN** o navegador processa o layout
- **THEN** o service worker é registrado e sua lógica ignora
  requisições POST e rotas de PDF

#### Scenario: Layout declara PWA

- **GIVEN** qualquer página do app
- **WHEN** o HTML é renderizado
- **THEN** inclui o link do manifest e a meta theme-color
