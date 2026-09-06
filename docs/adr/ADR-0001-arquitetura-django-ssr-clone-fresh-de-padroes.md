# ADR-0001: Arquitetura Django SSR — clone fresh de padrões do ats-web

## Status

Accepted

## Contexto

O HMD (apoio à regulação do serviço de hemodinâmica do HGRS) nasce do zero. O
projeto `ats-web` (triagem EDA, também Django SSR) é a referência arquitetural
validada em produção: mesmo domínio regulatório, mesmos operadores NIR/médico/
agendador e mesma operação via relatórios da Central de Regulação. Duas opções
se apresentaram: partir de um fork do `ats-web` e limpar, ou clonar padrões e
escrever o scaffold novo, arquivo por arquivo.

## Decisão

- Monolito **Django 5.2 SSR** com templates, sem API REST/SPA e sem DRF,
  espelhando a arquitetura validada do `ats-web` (curva de onboarding zero e
  runbooks reaproveitáveis).
- **Clone fresh de padrões, não fork**: cada arquivo do HMD é escrito para o
  contexto HMD consultando o equivalente do `ats-web` (fonte somente-leitura),
  sem copiar código às cegas.
- Stack: Python 3.13, Django 5.2 LTS, PostgreSQL 17 (psycopg 3), `uv` com
  lockfile versionado, ruff (lint+format, linha 100), mypy + django-stubs
  (strict desde o primeiro commit), pytest + pytest-django, Bootstrap 5.3 via
  CDN, WhiteNoise para estáticos.
- Settings por ambiente em `config/settings/{base,dev,prod,test}`; segredos e
  valores por ambiente vêm do ambiente (12-factor); `prod` falha fechado.

## Alternativas Consideradas

1. **Fork do ats-web com limpeza** — rejeitada: carregaria decisões legadas
   (estados Matrix, prompts EDA, django-fsm) que o roadmap do HMD muda
   explicitamente (viewflow.fsm, estados renomeados, 13 tipos de procedimento).
2. **Framework/arquitetura diferente** (ex.: API + SPA) — rejeitada: quebraria
   a paridade operacional com o ats-web e adicionaria superfície nova sem
   benefício para o domínio.

## Consequências

- Positivas:
  - Base testada em produção como referência de padrões; decisões de domínio
    (papéis, guard de intranet, filas) reaproveitam desenho do ats-web.
  - Toolchain de qualidade determinística roda desde o primeiro commit.
- Negativas/Trade-offs:
  - Cada adaptação exige leitura consciente do equivalente no ats-web — custo
    de implementação maior que um fork por arquivo.
  - Dependências legadas do ats-web que ainda serão necessárias (ex.: fila de
    tarefas) entram de forma incremental nos changes corretos, não no bootstrap.
