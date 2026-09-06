# project-infrastructure Specification (delta)

## Purpose

Fundação executável do repositório HMD: configuração por ambiente, ambiente de desenvolvimento reproduzível e quality gate local determinístico, estabelecendo a base sobre a qual todos os changes posteriores serão construídos.

## ADDED Requirements

### Requirement: Quality gate local determinístico

O repositório SHALL executar, a partir da raiz, um conjunto fixo de comandos de qualidade que devem terminar com exit 0 em uma árvore limpa: lint, verificação de formato, verificação de tipos e suíte de testes.

#### Scenario: Gate completo em árvore limpa

- **GIVEN** o repositório com dependências instaladas (`uv sync`) e o banco de teste disponível
- **WHEN** são executados na raiz `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .` e `uv run pytest`
- **THEN** todos os comandos terminam com exit 0

### Requirement: Configuração por ambiente com variáveis externas

As configurações sensíveis ou variáveis por ambiente (chave secreta, modo debug, hosts permitidos, origens confiáveis de CSRF e credenciais de banco) SHALL ser fornecidas exclusivamente por variáveis de ambiente, com valores-padrão seguros apenas em desenvolvimento.

#### Scenario: Precedência de DATABASE_URL

- **GIVEN** a variável `DATABASE_URL` definida apontando para um PostgreSQL
- **WHEN** a aplicação carrega as settings
- **THEN** o banco de dados configurado é o da `DATABASE_URL`, ignorando variáveis individuais `DB_*` concorrentes

#### Scenario: Variáveis individuais DB_* como fallback

- **GIVEN** `DATABASE_URL` não definida e as variáveis `DB_*` necessárias definidas
- **WHEN** a aplicação carrega as settings
- **THEN** o banco configurado é montado a partir das variáveis `DB_*`

#### Scenario: Segredo ausente em produção

- **GIVEN** settings de produção carregadas sem `DJANGO_SECRET_KEY`
- **WHEN** a aplicação inicializa
- **THEN** a inicialização falha imediatamente com erro explícito, sem usar valor-padrão

### Requirement: Ambiente de desenvolvimento reproduzível via docker compose

O repositório SHALL fornecer composições Docker que sobem o PostgreSQL 17 (com extensões `unaccent` e `pg_trgm` habilitadas no banco padrão) e a aplicação web em modo desenvolvimento, sem passos manuais além de instalar dependências e definir o `.env`.

#### Scenario: Subir ambiente dev e aplicar migrações

- **GIVEN** `.env` configurado e Docker disponível
- **WHEN** o banco de desenvolvimento é iniciado via compose e `uv run python manage.py migrate` é executado
- **THEN** as migrações são aplicadas com sucesso contra o banco do compose

### Requirement: Settings de teste isoladas

A suíte de testes SHALL rodar com settings próprios que isolam o banco de dados de teste (banco distinto, efêmero) e usam hashers de senha rápidos, sem alterar o ambiente de desenvolvimento.

#### Scenario: pytest usa banco de teste dedicado

- **GIVEN** o banco de teste efêmero disponível (compose de teste ou equivalente)
- **WHEN** `uv run pytest` é executado
- **THEN** os testes criam e usam um banco de dados de teste separado do banco de desenvolvimento
