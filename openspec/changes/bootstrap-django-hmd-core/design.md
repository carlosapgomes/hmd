# Design: bootstrap-django-hmd-core

## Context

Primeiro change do repositório HMD. O roadmap aprovado (`temp/plano-implementacao-hmd.md`) define 11 changes; este cria a fundação que todos dependem. O projeto fonte `/projects/dev/ats-web` é a referência arquitetural validada em produção (ver `temp/research/ats-web-recon.md`); o HMD clona **padrões**, não código: cada arquivo é escrito para o contexto HMD, consultando o equivalente do ats-web como referência somente-leitura.

## Goals / Non-Goals

**Goals**

- Monolito Django 5.2 SSR executável localmente em minutos (`uv sync` + compose + migrate).
- Quality gate determinístico (ruff/format/mypy/pytest) rodando desde o primeiro commit.
- App `apps/accounts` completo para papéis/papel ativo/guard de intranet — sem retrabalho nos changes seguintes.
- Documentação viva (AGENTS/PROJECT_CONTEXT/ADRs) que guie implementadores com contexto zero.

**Non-Goals**

- FSM, `Case`, catálogo de procedimentos (change `case-core-fsm-procedures`).
- Autenticação AD/Kerberos, `ad_upn`, subtipos de doctor (changes `ad-kerberos-authentication` e `case-core-fsm-procedures`).
- django-q2/workers, Presidio, pipeline LLM, intake, dashboard, PWA (changes posteriores).
- E-mails transacionais — **fora de escopo permanente** (ADR-0003): senha será do AD; não há reset/convite por e-mail.
- CI remota (GitHub Actions) — o gate local é o contrato; pipeline remoto pode ser adicionado em change posterior sem impacto de spec.

## Decisions

### D1 — Clone fresh de padrões, não fork do ats-web

Scaffold novo, app por app, lendo os arquivos equivalentes do ats-web durante a implementação.
*Alternativa rejeitada*: fork do repositório e limpeza — carregaria decisões legadas (estados Matrix, prompts EDA, django-fsm) que o roadmap muda (viewflow.fsm, estados renomeados, 13 tipos de procedimento).

### D2 — Stack e versões

Python 3.13, Django 5.2 LTS, PostgreSQL 17 (psycopg 3), `uv` (lockfile versionado), ruff (lint+format), mypy + django-stubs, pytest + pytest-django, Bootstrap 5.3 via CDN, WhiteNoise para static. Sem dependências além destas neste change.
*Justificativa*: idêntico ao ats-web (curva zero, runbooks reaproveitáveis), exceto o que o roadmap muda explicitamente (sem django-fsm, sem django-q2 ainda).

### D3 — Estrutura de settings

`config/settings/{base,dev,prod,test,db}.py` espelhando o padrão ats-web: `db.py` resolve `DATABASE_URL` com precedência sobre `DB_*` (incluindo `DB_PASSWORD_FILE` para secret de compose); `test.py` usa banco dedicado (porta/instância própria) e hashers MD5; `prod.py` falha fechado sem `DJANGO_SECRET_KEY` e sem DEBUG. **Isolamento do banco de teste**: `test.py` resolve a configuração por `TEST_DATABASE_URL` com precedência sobre `TEST_DB_*` — e **ignora** `DATABASE_URL`/`DB_*` de desenvolvimento — garantindo que a suíte nunca toque o banco dev mesmo com `.env` carregado; teste dedicado afirma nome/porta distintos.
*Variáveis novas HMD*: `APP_DISPLAY_NAME` (default "HMD — Hemodinâmica"). Variáveis de e-mail do ats-web **não** existem aqui.

### D4 — Papel ativo em sessão (padrão ats-web)

`ActiveRoleMiddleware` define `request.session["active_role"]`: 1 papel → auto-set; N papéis → redirect para `/switch-role/`; 0 papéis → logout com mensagem. Context processor expõe papel ativo + papéis do usuário a todos os templates. Referências: `ats-web: apps/accounts/middleware.py`, `context_processors.py`, `views.py` (switch_role), `decorators.py`.
*Alternativa rejeitada*: papel como campo do User — conflita com multi-role simultâneo e com sessões múltiplas do mesmo usuário.

### D5 — Intranet guard restrito apenas a `nir`, decidido pelo papel ATIVO

`IntranetGuardMiddleware` bloqueia quando o **papel ativo** da sessão pertence a `INTRANET_RESTRICTED_ROLES` (env, default `["nir"]`) e o IP de origem está fora de `INTRANET_IP_RANGE` (CIDRs separadas por vírgula). IP de origem lido de `TRUSTED_PROXY_HEADER` (default `HTTP_CF_CONNECTING_IP`, túnel Cloudflare). Paths isentos: login/logout/switch-role (+ static/media) — o switch-role isento é a **rota de fuga** do multi-role: usuário `nir`+`manager` bloqueado externamente com papel ativo `nir` pode trocar para `manager` e voltar a acessar.
*Divergência deliberada vs ats-web*: lá o bypass considera o **conjunto** de papéis (usuário com qualquer papel não-restrito passa); no HMD a restrição segue o papel ativo — a pessoa está exercendo a função `nir` no momento, e a troca de papel continua disponível via path isento. Nota: no ats-web `INTRANET_RESTRICTED_ROLES` é constante em `apps/accounts/middleware.py`; no HMD vira setting por env (mesma funcionalidade, configurável). Decisão registrada em ADR-0002.

### D6 — Autenticação local transitória

ModelBackend padrão + views de login/logout próprias (templates HMD). `account_status` (`active|blocked|removed`) validado no backend autal (backend custom mínimo que delega checagem de senha ao ModelBackend e recusa não-ativos). `superuser` local persiste como break-glass mesmo após o change 02 (ADR-0003).
*Alternativa rejeitada*: já usar minikerberos aqui — acoplaria o bootstrap à rede do hospital e invalidaria o gate local determinístico.

### D7 — Testes com PostgreSQL (paridade desde o início)

`docker-compose.test.yml` sobe Postgres efêmero (tmpfs, porta 5433, banco `hmd_test`); `pytest` usa `--reuse-db`. `conftest.py` na raiz seleciona settings de teste.
*Alternativa rejeitada*: SQLite em testes — mudaria dialect/índices justamente nos changes que usarão `pg_trgm`/`unaccent` (dashboard), forçando migração tardia da suíte.

### D8 — User model estendido uma única vez

`apps/accounts.User(AbstractUser)`: `roles` M2M(`Role`), `account_status`, `professional_council` (`CRM|COREEN`) + `professional_council_number` (validação par-ou-nada no `clean()` com `ValidationError` **associada ao campo** — `{"professional_council": ...}`/`{"professional_council_number": ...}`; o ats-web lança erro não associado a campo, divergência deliberada para UX de formulário), `display_name` derivado (`get_full_name() or username`). Campos futuros (`ad_upn`, `specialties`) chegam com migrations nos changes 02/03 — modelo central não volta a mudar neste change além do listado.

### D9 — Templates e tema

`templates/base.html` com o tema hospitalar do ats-web adaptado (navbar com nome do app, papel ativo, menu de troca de papel, avatar/perfil), `static/css/app.css` reescrito para a paleta HMD, páginas: login, switch-role, perfil (visualização + troca de senha local enquanto a auth local existir), home por papel (placeholder simples que os changes posteriores substituem pelas filas reais).

### D10 — Documentação viva desde o slice 001

`AGENTS.md` (regras, stack, comandos, política de testes, workflow OpenSpec com slices verticais e TDD, política de commit), `PROJECT_CONTEXT.md` (fontes autoritativas, objetivo, roadmap resumido com os 11 changes), `docs/adr/` com ADR-0001 (arquitetura SSR), ADR-0002 (papéis + guard nir-only), ADR-0003 (auth em dois estágios + sem e-mails). Slices posteriores de outros changes atualizam `PROJECT_CONTEXT.md` ao arquivar.

## Risks / Trade-offs

- [Auth local transitória vira superfície de ataque esquecida] → ADR-0003 marca explicitamente a substituição; change 02 remove o login local comum e mantém só break-glass via flag.
- [Sem CI remota neste change] → gate local documentado em AGENTS.md é o contrato de verificação; adicionar CI depois não muda spec.
- [Porta 5433 fixa pode colidir com ambiente local] → configurável por env (`TEST_DB_PORT`), documentado no `.env.example`.
- [Mypy com Django exige stubs/config rigorosa] → `django-stubs` + plugin mypy configurado desde o primeiro commit; samples de código já nascem tipados.

## Migration Plan

Projeto novo, sem dados. Implantação: `uv sync` → `docker compose up -d db` → `uv run python manage.py migrate` → `uv run python manage.py seed_admin`. Rollback: inexistente (first deploy).

## Open Questions

(nenhuma — decisões pendentes pertencem aos changes 02+ e estão registradas no roadmap)
