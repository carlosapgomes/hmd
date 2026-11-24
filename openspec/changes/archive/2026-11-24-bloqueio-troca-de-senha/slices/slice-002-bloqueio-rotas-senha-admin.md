# Slice 002 — Bloqueio das rotas de troca de senha do Django admin

## Identity

- Slice: S2
- Change: `bloqueio-troca-de-senha`
- Task: `tasks.md` → «S2 — Bloqueio das rotas de troca de senha do Django admin»
- Depende de: S1 aceito (parte do commit anterior)

## Objective

Fazer com que nenhuma rota de troca/definição de senha exista no aplicativo —
inclusive as três herdadas do Django admin — sem link morto e sem quebrar as
páginas do admin, e documentar o canal da TI para a senha do admin local.

## Read first

- `AGENTS.md`
- `PROJECT_CONTEXT.md` — nota operacional de porta do banco de teste
- `openspec/changes/bloqueio-troca-de-senha/proposal.md`
- `openspec/changes/bloqueio-troca-de-senha/design.md` — D3 a D7 (as duas
  armadilhas estão no Context)
- `openspec/changes/bloqueio-troca-de-senha/specs/account-access/spec.md` —
  requisito «Sem rotas de troca de senha no aplicativo»
- `config/admin.py` — `HmdAdminSite` (login custom) e `HmdAdminConfig`
- `apps/accounts/admin.py` — `UserAdmin` (`get_urls` e `form` herdados do
  Django; `save_model`; `fieldsets` incluem `password`)
- Django instalado em `.venv/lib/python3.14/site-packages/django/` (5.2.17),
  **somente-leitura**:
  - `contrib/admin/sites.py` — `get_urls` (padrões `password_change` e
    `password_change_done`), `catch_all_view`
  - `contrib/admin/templates/admin/base.html` — bloco `userlinks` (linhas
    56–58: o bloco de 3 linhas a remover)
  - `contrib/auth/admin.py` — `UserAdmin.get_urls` (padrão
    `auth_user_password_change`)
  - `contrib/auth/forms.py` — `ReadOnlyPasswordHashWidget`, `UserChangeForm`
  - `contrib/auth/templates/auth/widgets/read_only_password_hash.html`
- `apps/accounts/tests/test_ad_upn.py` — `TestAdminRegistration` (padrão
  `admin_client`); `test_admin_professional_council.py` (fixtures de admin);
  `test_local_admin.py` — `TestHmdAdminSiteLogin`
- `docs/adr/ADR-0009-admin-identidade-local-por-design.md` (molde de ADR)
- `README.md` — seção «Autenticação AD (Kerberos) e admin local por design»

## Requirements

- R1: `HmdAdminSite.get_urls()` não registra os padrões de nome
  `password_change` e `password_change_done`. Superusuário autenticado recebe
  **404** em `/admin/password_change/` e `/admin/password_change/done/`
  (GET e POST); `reverse("admin:password_change")` e
  `reverse("admin:password_change_done")` levantam `NoReverseMatch`.
- R2: `UserAdmin.get_urls()` não registra o padrão de nome
  `auth_user_password_change` (`reverse("admin:auth_user_password_change",
  args=[pk])` levanta `NoReverseMatch`). GET e POST a
  `/admin/accounts/user/<id>/password/` não exibem formulário de senha: a
  resposta é o redirect (302) do padrão de compatibilidade do `ModelAdmin` para
  a página de edição, que redireciona (302) ao índice do admin com o aviso de
  objeto inexistente — o teste usa `follow=True` e exige status final 200 no
  índice (`/admin/`) com a cadeia de redirects. POST válido com nova senha não
  altera a senha de usuário AD (segue sem senha utilizável) nem de usuário local
  (`check_password` da senha original segue verdadeiro). Observável definido
  por decisão do dono (2026-11-24; D3): nem o padrão de compatibilidade nem o
  redirect de objeto inexistente são filtrados ou sobrescritos.
- R3: `templates/admin/base.html` do projeto (override) renderiza, para o
  superusuário local — que tem senha utilizável, a condição do link original —
  as páginas index, changelist de usuários, formulário de edição e de criação
  de usuário, com 200, sem `NoReverseMatch`, e sem `password_change` no HTML
  de nenhuma delas.
- R4: O formulário de edição de usuário responde 200, não exibe o botão
  com href `../password/` nem o texto de ajuda padrão que instrui a definir
  senha; exibe o resumo do hash do campo e o texto exato «Gerida fora do
  aplicativo — a troca de senha é feita pessoalmente na TI do hospital.». O
  template do widget vive em
  `apps/accounts/templates/accounts/widgets/read_only_password_hash.html`
  (nível do app — decisão do dono, opção 1-a; D5).
- R5 (anti-drift): teste lê o `base.html` original do Django instalado e o do
  projeto e exige que `original.replace(BLOCO, "") == projeto`, onde `BLOCO` é
  exatamente as três linhas
  `{% if user.has_usable_password %}` /
  `<a href="{% url 'admin:password_change' %}">{% translate 'Change password' %}</a> /`
  / `{% endif %}` (com a indentação do original).
- R6 (varredura): teste percorre recursivamente todos os padrões do resolver
  do projeto (`get_resolver()`, incluindo `URLResolver` aninhados) e exige que
  nenhum caminho de rota nem nome de rota contenha `password`. Hoje a
  varredura encontra exatamente os três padrões do admin.
- R7: Testes existentes do admin (`test_ad_upn.py`,
  `test_admin_professional_council.py`, `test_local_admin.py`) seguem verdes
  **sem alteração de asserções**; `UserAdmin.save_model`, `add_fieldsets`
  (criação com senha — fora de escopo) e `HmdAdminSite.login` permanecem
  intactos.
- R8: `README.md` documenta a rotação da senha do admin local por
  `manage.py changepassword <username>` (TI, shell no host; em produção via
  compose) e o ADR `docs/adr/ADR-0011-troca-de-senha-fora-do-aplicativo.md` é
  criado no molde do ADR-0009 (D7), com uma linha de referência cruzada nas
  consequências do ADR-0009.
- Evidência: R1–R7 por testes automatizados (novo arquivo
  `apps/accounts/tests/test_admin_password_routes_removed.py`); R8 por
  inspeção dos arquivos entregues.

## Expected blast radius

- Esperado: `config/admin.py`, `apps/accounts/admin.py`,
  `templates/admin/base.html` (novo),
  `apps/accounts/templates/accounts/widgets/read_only_password_hash.html`
  (novo; primeiro diretório de templates por app do repo — decisão 1-a),
  teste novo em `apps/accounts/tests/`, `README.md`,
  `docs/adr/ADR-0011-troca-de-senha-fora-do-aplicativo.md` (novo),
  `docs/adr/ADR-0009-admin-identidade-local-por-design.md` (uma linha de
  referência cruzada).
- Sensível/intocável: `apps/accounts/backends.py`, `apps/accounts/kerberos.py`,
  `apps/accounts/ratelimit.py`, `apps/accounts/views.py`,
  `apps/accounts/models.py`, migrations, `templates/accounts/profile.html`
  (S1), `templates/accounts/login.html`, `add_fieldsets` e `save_model` do
  `UserAdmin`, comandos de management (`changepassword`, `seed_admin`), o
  Django instalado em `.venv`, e o `templates/` da raiz do projeto (o renderer
  de formulários não o consulta: não criar `templates/accounts/widgets/`).
- Comportamento preservado: anti-lockout do login do admin, provisionamento
  com `ad_upn` (senha inutilizada), fieldsets «Acesso institucional» e
  «Profissional», `filter_horizontal`, `list_display`.
- Expansão de blast radius exige justificativa no relatório.

## Failing-before plan

Failing-before evidence:
- targeted test: novo
  `uv run pytest apps/accounts/tests/test_admin_password_routes_removed.py -x`
  (escrito primeiro)
- expected failure: `/admin/password_change/` responde 200 (GET) e 302 após
  alterar a senha do superusuário (POST); a rota de senha do usuário responde
  200 (GET) e 302 após gravar senha (POST); o index do admin contém
  `password_change` no HTML; o formulário de edição contém `../password/` e o
  texto de ajuda padrão; a varredura (R6) encontra três padrões; R5 falha por
  ausência de `templates/admin/base.html`.

## Implementation constraints

- Filtragem por **nome** do padrão em `get_urls` (nunca por índice/posição);
  os demais padrões de `super().get_urls()` seguem na ordem original.
- O template sobrescrito é cópia byte a byte do original, exceto o bloco de
  três linhas (R5 prova); não «melhorar», traduzir nem reformatar o restante.
- Widget: subclasse de `ReadOnlyPasswordHashWidget` com `template_name`
  próprio; o template é o do Django sem a linha do botão. Help text exatamente
  como em R4.
- Não criar view que responda 404 no lugar da rota (D3): a rota deixa de
  existir.
- Template do widget no nível do app (`apps/accounts/templates/…`): o renderer
  de formulários só busca em `django/forms/templates` + `templates/` dos apps
  instalados; NÃO criar `templates/accounts/widgets/` (D5, decisão 1-a).
- NÃO filtrar nem substituir o padrão de compatibilidade `<path:object_id>/`
  do `ModelAdmin` nem sobrescrever o redirect de objeto inexistente (D3,
  decisão do dono 2026-11-24): o contrato da rota por usuário é «sem página de
  senha, sem escrita, redirect ao índice do admin», provado com `follow=True`.
- Não remover os campos de senha do formulário de **criação**
  (`add_fieldsets`: `usable_password`, `password1`, `password2`) — fora de
  escopo por decisão do dono.
- ADR novo, curto (Status/Contexto/Decisão/Alternativas/Consequências) — não
  emendar o ADR-0009 além da linha de referência.

## Validation

Focused validation (worker):
- `uv run pytest apps/accounts/tests/test_admin_password_routes_removed.py -x`
- `uv run pytest apps/accounts -x`
- `uv run python manage.py check --settings=config.settings.dev`

Project-required gates: `AGENTS.md` §2 (ruff, format, mypy, pytest).

Runtime/verification evidence: não exigida (superfície de admin coberta por
teste de client; não há jornada `verify-hmd` para admin).

## Acceptance criteria

- [ ] R1 rotas de autoatendimento do admin ausentes (404 + `NoReverseMatch`)
- [ ] R2 rota de senha por usuário ausente (`NoReverseMatch`; `follow=True`
      termina no índice do admin); nenhuma senha alterada
- [ ] R3 páginas do admin renderizam sem `password_change` nem erro
- [ ] R4 formulário de usuário sem botão, com texto neutro e resumo do hash
- [ ] R5 anti-drift do template verde
- [ ] R6 varredura do resolver: zero padrões com `password`
- [ ] R7 testes existentes do admin verdes sem mudança de asserções
- [ ] R8 README + ADR-0011 (+ referência no ADR-0009) entregues
- [ ] Failing-before evidence registrada
- [ ] Gates do projeto verdes no escopo tocado

## Escalation conditions

Pare e retorne `BLOCKED_NEEDS_DECISION` se: contradição com spec/design;
necessidade de remover os campos de senha do formulário de criação; pedido de
manter rota de senha com resposta 404/403; necessidade de alterar
backends/anti-lockout/login do admin; necessidade de editar o Django instalado;
expansão material de escopo.

## Evidence report

`/tmp/hmd-slice-bloqueio-senha-002-report.md` — status
(`READY_FOR_REVIEW`/`BLOCKED_NEEDS_DECISION`), requirements→evidence,
arquivos alterados, failing-before, passing-after, comandos+exit, riscos.

## Worker handoff

Implement only this approved slice.
Treat the slice and referenced OpenSpec artifacts as the implementation contract.
Reconstruct context from the listed files; do not rely on prior conversation.
Follow the repository engineering policy in `AGENTS.md` and preserve existing
conventions. Prefer the smallest correct change; avoid speculative abstraction
and unrelated refactoring.
Produce failing-before evidence, implement the smallest correct change, run
focused validation, and produce the required evidence report.
Do not update `tasks.md`, commit, push, merge, archive, or start another slice.
If a new decision is required, stop with `BLOCKED_NEEDS_DECISION` and evidence.
Otherwise finish with `READY_FOR_REVIEW` and `REPORT_PATH=<path>`.
