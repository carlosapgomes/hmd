# Design

## Context

- Ver `proposal.md` — Why. Enumeração autoritária do resolver (varredura de
  todos os `URLPattern`, 2026-11-24, Django 5.2.17): quatro superfícies de
  troca de senha — `POST /profile/` e três rotas do Django admin
  (`admin:password_change`, `admin:password_change_done`,
  `admin:auth_user_password_change`). Nenhuma rota de
  `django.contrib.auth.urls` está incluída em `config/urls.py`.
- **Armadilha 1 — a barra do admin**: `django/contrib/admin/templates/admin/base.html`
  (bloco `userlinks`) renderiza
  `<a href="{% url 'admin:password_change' %}">` quando `user.has_usable_password`
  — verdadeiro para o admin local. Remover a rota sem tratar o template
  levanta `NoReverseMatch` (500) em **toda** página do admin para esse
  usuário.
- **Armadilha 2 — o formulário de usuário**: o `ReadOnlyPasswordHashWidget` do
  `UserChangeForm` renderiza um botão com `href` **relativo** `../password/`
  (sem `reverse`). Após remover a rota, o botão vira link morto; e o
  `help_text` padrão instrui a «definir uma senha» para usuários sem senha
  utilizável.
- **Descoberto na implementação do S2 (2026-11-24, verificado no Django
  5.2.17)**: (i) o renderer de formulários (`django/forms/renderers.py::
  EngineMixin`) usa engine próprio — `django/forms/templates` + `templates/`
  dos apps instalados — e **não** consulta o `DIRS` do projeto; `django.forms`
  não está em `INSTALLED_APPS`, então `TemplatesSetting` também não resolveria
  os templates base; (ii) o `ModelAdmin` registra o padrão de compatibilidade
  `<path:object_id>/` (`RedirectView`, GET e POST) para a página de edição —
  sem a rota de senha, `/admin/accounts/user/<id>/password/` casa esse padrão
  com `object_id="<id>/password"`: 302 → 302 → 200 no índice do admin (aviso
  de objeto inexistente), sem página de senha e sem escrita — o
  `_get_obj_does_not_exist_redirect` do `ModelAdmin` nunca responde 404.
- `UserAdmin.user_change_password` grava por `form.save()` (`set_password`
  direto), ignorando o `save_model` do projeto, que aplica
  `set_unusable_password()` a usuários com `ad_upn`.
- Para usuário AD o formulário do perfil nunca funcionou: `check_password`
  devolve `False` para senha inutilizável.
- Admin local (ADR-0009): `seed_admin` em re-execuição não reseta a senha;
  `manage.py changepassword` (comando embutido do Django) existe e roda só por
  shell no host — o canal da TI.
- A spec `account-access` não pinna o formulário do perfil; o perfil continua
  coberto pelo requisito «Rótulos de papel em português na interface».
- Harness `verify-hmd`: `drive-session-pages.mjs` exige `profile-cards >= 2`
  e `features/sessoes.md` descreve «cards de dados/alteração de senha» — ambos
  rastreados no Git e precisam acompanhar a mudança.

## Goals / Non-Goals

**Goals:**

- Nenhuma superfície do aplicativo sugere ou permite troca de senha.
- Perfil continua exibindo os dados da conta e os rótulos de papel.
- Django admin 100% funcional para o admin local, sem link morto.
- Regressões protegidas por teste: 405 no perfil, 404/ausência das rotas do
  admin, varredura do resolver e anti-drift do template sobrescrito.

**Non-Goals:**

- Remover os campos de senha do formulário de **criação** de usuário
  (`add_fieldsets`: `usable_password`, `password1`, `password2`). É criação da
  identidade local, não troca; para usuários com `ad_upn` o `save_model` já
  inutiliza a senha (spec vigente). Mantido fora de escopo por decisão do dono
  (2026-11-24).
- Comandos de CLI (`manage.py changepassword`, `seed_admin`,
  `createsuperuser`) — canal da TI, não são endpoints.
- Login, toggle de senha do login, anti-lockout, backends.
- Reset de senha por e-mail (fora de escopo permanente — ADR-0003).
- Manual de usuário: não menciona senha; sem mudança necessária.

## Decisions

### D1 — Perfil é leitura; escrita responde 405

`profile_view` mantém `@login_required` e ganha `@require_GET` (ordem:
`login_required` por fora, `require_GET` por dentro — mesmo par do
`notifications_unread_count`; anônimo continua indo ao login). `POST` deixa de
ser processado: 405, nenhuma leitura de `request.POST`, nenhuma escrita.
`HospitalPasswordChangeForm` e o import de `update_session_auth_hash` saem
(código morto).

Alternativas: (a) re-renderizar o perfil com mensagem de erro — rejeitada:
«bloquear» deve ser explícito, e 200 esconderia a recusa; (b) remover a rota
`profile/` e criar outra só-leitura — rejeitada: quebra links/bookmarks sem
ganho; 405 já comunica o método não permitido.

### D2 — Layout e texto de orientação

O card «Dados da conta» passa de `col-12 col-md-6` para `col-12` (largura
total; sem coluna vazia). Ao final do corpo do card, um parágrafo de texto
secundário com o texto **exato**:

> Precisa trocar a senha? Procure pessoalmente a TI do hospital.

Sem link, botão ou formulário. O texto é pinado por teste e pela jornada
`sessoes` do `verify-hmd`, para que não reapareça uma affordance de troca.
Alternativa: card próprio «Senha» — rejeitada: bloco dedicado a senha recria a
expectativa que se quer eliminar.

### D3 — Rotas do admin removidas na origem, por nome

Em vez de manter as rotas respondendo 404, elas deixam de ser registradas:
`HmdAdminSite.get_urls()` filtra de `super().get_urls()` os padrões chamados
`password_change` e `password_change_done`; `UserAdmin.get_urls()` faz o
mesmo com `auth_user_password_change`. A filtragem é por **nome**, não por
posição — tolera reordenação de padrões em upgrades do Django. Consequências:
`reverse()` dos três nomes levanta `NoReverseMatch` (prova forte em teste); as
requisições caem no `final_catch_all_view` do admin → 404 para autenticado
(anônimo: redirect ao login do admin e 404 em seguida). Nenhum código do
projeto chama `reverse` para esses nomes.

Exceção medida para a rota **por usuário**: `/admin/accounts/user/<id>/password/`
não chega ao catch-all — casa o padrão de compatibilidade `<path:object_id>/`
do `ModelAdmin` (redirect 302, GET e POST, para a página de edição), que, para
objeto inexistente, chama `_get_obj_does_not_exist_redirect` — que **nunca**
responde 404: redireciona (302) ao índice do admin com o aviso «… não existe».
Cadeia real: 302 → 302 → 200 no índice, sem página de senha e sem escrita em
nenhum passo. Por decisão do dono (2026-11-24), esse é o observável aceito
para a rota por usuário; o padrão de compatibilidade e o redirect de objeto
inexistente **não** são filtrados, substituídos nem sobrescritos (fazê-lo
mudaria o comportamento legado de todos os objetos do admin ou acoplaria a um
método privado do `ModelAdmin`). As duas rotas de autoatendimento seguem com
404 direto pelo catch-all.

Alternativas: (a) manter rotas e responder 404/403 nas views — rejeitada: os
nomes continuam resolvíveis e exige views mortas mantidas para sempre;
(b) `final_catch_all_view = False` — rejeitada: muda o tratamento de todo URL
 desconhecido do admin.

### D4 — Override de `admin/base.html` com guarda anti-drift

`templates/admin/base.html` do projeto é uma **cópia fiel** do
`django/contrib/admin/templates/admin/base.html` da versão instalada
(5.2.17), alterada apenas na remoção do `<a>` «Alterar senha» do bloco
`userlinks` (o `{% if user.has_usable_password %}` que o envolve vai junto).
Necessário por causa do `{% url %}` (Armadilha 1).

Proteção anti-drift: um teste lê o original do Django instalado
(`pathlib.Path(django.__file__).parent / "contrib/admin/templates/admin/base.html"`)
e a cópia do projeto, e exige igualdade após remover do original exatamente o
bloco do link. Um upgrade do Django que toque o `base.html` quebra o teste e
aponta a ressincronização — mesma classe de risco já registrada como P2 do
change `admin-local-identity` («sincronizar em upgrades do Django»).

Alternativas: (a) esconder o link por CSS — rejeitada: o link continua no DOM
e o seletor é frágil; (b) estender o original com
`{% extends "admin/base.html" %}` a partir do override — inviável: o loader
resolve o próprio override (recursão).

### D5 — Formulário de edição de usuário sem botão e com texto neutro

`UserAdmin.form` passa a ser um `UserChangeForm` do projeto cujo campo
`password` usa um widget próprio: subclasse de `ReadOnlyPasswordHashWidget`
com `template_name` apontando para
`apps/accounts/templates/accounts/widgets/read_only_password_hash.html` — o
template do Django sem a linha do botão. O template vive no **nível do app**
(decisão do dono, 2026-11-24, opção 1-a) porque o renderer de formulários só
busca em `django/forms/templates` + `templates/` dos apps instalados: em
`templates/accounts/widgets/` (DIRS do projeto) o resultado medido foi
`TemplateDoesNotExist` (500 no formulário de edição). É o primeiro diretório
de templates por app do repo, coerente com o AGENTS.md §4 («app autocontido:
models/views/templates»). O resumo do hash (algoritmo/última alteração)
permanece: é informação útil à TI. O `help_text` do campo vira, para qualquer
usuário: «Gerida fora do aplicativo — a troca de senha é feita pessoalmente na
TI do hospital.»

Alternativas: sobrescrever `auth/widgets/read_only_password_hash.html` pelo
mesmo nome — rejeitada: shadowing global silencioso; template nomeado no
projeto é explícito e não afeta outros usos. Renderer `TemplatesSetting` no
form — rejeitada: com `django.forms` fora de `INSTALLED_APPS`, os templates
base dos formulários não seriam encontrados sem mexer em settings;
`FORM_RENDERER` global — fora de escopo.

### D6 — Rotação da senha do admin local fora do aplicativo

A identidade local perde o autoatendimento (as duas rotas que ela podia
usar — perfil e `/admin/password_change/` — saem). O caminho passa a ser
`manage.py changepassword <username>` executado pela TI no host (em produção,
via `docker compose … exec web …`). `seed_admin` permanece sem resetar a senha
em re-execução (comportamento vigente preservado). Documentado em `README.md`
(seção «Autenticação AD (Kerberos) e admin local por design»).

### D7 — ADR novo, não emenda

A decisão é sobre a política de senha do aplicativo inteiro (AD e admin
local), não só sobre a identidade local do ADR-0009 — portanto ADR novo
(`ADR-0011`), curto: contexto, decisão, alternativas, consequências. O
ADR-0009 recebe apenas uma referência cruzada na seção de consequências.

## Risks / Trade-offs

- **[Admin em 500 por `NoReverseMatch` ao remover `admin:password_change`]** →
  D4; e os testes existentes que renderizam páginas do admin como superusuário
  (`test_ad_upn.py`, `test_admin_professional_council.py`,
  `test_local_admin.py`) funcionam como rede de regressão sem alteração.
- **[Upgrade do Django altera `admin/base.html`]** → teste anti-drift de D4
  falha e aponta a ressincronização.
- **[Teste existente pinna a troca atual
  (`test_auth_flow.py::test_profile_password_change`)]** → invertido no S1
  (RED → GREEN), não apagado.
- **[Harness `verify-hmd` exige 2 cards no perfil]** → atualizado no S1 com
  checagem negativa; jornada `sessoes` reexecutada.
- **[Admin local perde o autoatendimento de senha]** → documentado em
  README/ADR; TI roda `changepassword`. Sem janela de indisponibilidade: a
  senha atual continua válida.
- **[Usuário AD que também seja `is_staff`]** — pelo perfil e pelo
  `/admin/password_change/` a troca já falhava (senha inutilizável); as rotas
  somem do mesmo jeito, e o botão de «definir senha» por usuário — o único
  caminho que de fato gravaria — sai junto.
- **[Tradução pt-br dos textos do admin]** → os testes do link procuram o
  `href`/`password` no HTML e não só o texto traduzido.

## Migration Plan

- Sem migration de banco nem dado persistido envolvido. Deploy normal; rollback
  = reverter o commit.
- Operação: comunicar à TI que a senha do admin local passa a ser rotacionada
  por `manage.py changepassword` (antes feita no perfil ou no admin).
