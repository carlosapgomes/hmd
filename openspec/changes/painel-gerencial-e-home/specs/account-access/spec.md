# account-access Specification (delta)

## ADDED Requirements

### Requirement: Home por papel ativo

Após o login (e em qualquer navegação à home), o sistema SHALL direcionar o usuário à área de trabalho do seu papel ativo: `nir` → formulário de envio de relatório; `doctor` → fila médica; `scheduler` → fila de agendamento; `manager`/`admin` → painel gerencial. Sem papel ativo válido, a home SHALL exibir a tela de boas-vindas como fallback.

#### Scenario: NIR cai no envio de relatório

- **GIVEN** um usuário autenticado com papel ativo `nir`
- **WHEN** navega à home
- **THEN** é redirecionado ao formulário de envio de relatório

#### Scenario: Doctor e scheduler caem nas suas filas

- **GIVEN** um usuário autenticado com papel ativo `doctor` (ou `scheduler`)
- **WHEN** navega à home
- **THEN** é redirecionado à fila médica (ou à fila de agendamento)

#### Scenario: Manager e admin caem no painel

- **GIVEN** um usuário autenticado com papel ativo `manager` (ou `admin`)
- **WHEN** navega à home
- **THEN** é redirecionado ao painel gerencial

#### Scenario: Sem papel ativo válido exibe fallback

- **GIVEN** um usuário autenticado sem papel ativo na sessão
- **WHEN** navega à home
- **THEN** vê a tela de boas-vindas (fallback), sem redirect
