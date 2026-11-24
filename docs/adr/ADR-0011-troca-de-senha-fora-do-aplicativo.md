# ADR-0011: Troca de senha fora do aplicativo

## Status

Accepted

## Contexto

A senha das identidades do HMD não é gerida pelo aplicativo: a das
atividades assistenciais é a do Active Directory (AD) e a do admin local
(ADR-0009) é uma identidade permanente fora do AD. Ainda assim, o Django
admin herdava três rotas de troca/definição de senha sem que o projeto as
tivesse pedido (`admin:password_change`, `admin:password_change_done` e
`admin:auth_user_password_change`, esta última por usuário). A remoção
ingênua dessas rotas quebra todo o admin: o `admin/base.html` original
renderiza `{% url 'admin:password_change' %}` para quem tem senha
utilizável (o admin local) e levantaria `NoReverseMatch` em todas as
páginas; o formulário de edição de usuário, por sua vez, exibe um botão
com `href` relativo `../password/` que viraria link morto.

## Decisão

- **Rotas removidas na origem, por nome**: `HmdAdminSite.get_urls()` filtra
  `password_change`/`password_change_done` e `UserAdmin.get_urls()` filtra
  `auth_user_password_change` de `super().get_urls()`. As rotas de
  autoatendimento passam a responder 404 (catch-all do admin) e
  `reverse()` dos três nomes levanta `NoReverseMatch`.
- **Override de `admin/base.html` com guarda anti-drift**: o template do
  projeto é cópia fiel do original do Django 5.2.17 menos exatamente o
  bloco de três linhas do link «Change password»; um teste exige essa
  igualdade, de modo que um upgrade do Django que toque o `base.html`
  quebra o teste e aponta a ressincronização.
- **Widget sem botão, template no nível do app**: o campo `password` do
  formulário de edição usa widget próprio (subclasse de
  `ReadOnlyPasswordHashWidget`) cujo template vive em
  `apps/accounts/templates/accounts/widgets/read_only_password_hash.html` —
  é o do Django sem a linha do botão `../password/`. O resumo do hash
  permanece; o `help_text` é «Gerida fora do aplicativo — a troca de senha
  é feita pessoalmente na TI do hospital.» para qualquer usuário.
- **Rota por usuário com o comportamento nativo do Django**: sem a rota de
  senha, `/admin/accounts/user/<id>/password/` casa o padrão de
  compatibilidade `<path:object_id>/` do `ModelAdmin` (302 para a página
  de edição), que, para o objeto inexistente `<id>/password`, redireciona
  (302) ao índice do admin com o aviso de objeto inexistente. Nenhuma
  página de senha, nenhuma escrita — e nem o padrão de compatibilidade nem
  o redirect de objeto inexistente são filtrados ou sobrescritos.
- **Criação de usuário com senha fora de escopo**: `add_fieldsets`
  (`usable_password`, `password1`, `password2`) permanece intacto — é
  criação de identidade local, não troca.
- **Canal da TI**: a senha do admin local é rotacionada fora do aplicativo
  com `manage.py changepassword <username>` (shell no host; em produção
  via compose), documentado no README.

## Alternativas Consideradas

1. **Views que respondem 404 no lugar das rotas** — rejeitadas: os nomes
   continuariam reversíveis e exigiriam views mortas mantidas para sempre;
   a rota deixa de existir.
2. **Filtrar o padrão de compatibilidade ou sobrescrever o redirect de
   objeto inexistente para forçar 404 na rota por usuário** — rejeitadas:
   mudaria o comportamento legado de todos os objetos do admin ou acoplaria
   o projeto a um método privado do `ModelAdmin`; o observável nativo
   (redirect ao índice, sem página de senha e sem escrita) é suficiente.
3. **Esconder o link por CSS** — rejeitada: o link continuaria no DOM e o
   seletor seria frágil.
4. **Renderer `TemplatesSetting` no formulário** — rejeitada: com
   `django.forms` fora de `INSTALLED_APPS`, os templates base dos
   formulários não seriam encontrados sem mexer em settings.
5. **Shadowing de template** (`auth/widgets/read_only_password_hash.html`
   pelo mesmo nome) — rejeitada: troca global silenciosa; o template com
   nome próprio do projeto é explícito e não afeta outros usos.

## Consequências

- **O admin local perde o autoatendimento de senha** (perfil e
  `/admin/password_change/` saem): a rotação passa a ser pela TI com
  `manage.py changepassword <username>`; sem janela de indisponibilidade —
  a senha atual continua válida.
- **Upgrade do Django que mude o `admin/base.html` quebra o teste
  anti-drift** e exige ressincronizar o override do projeto.
- Positiva: nenhuma superfície do aplicativo sugere ou permite troca de
  senha — nem autoatendimento, nem por usuário, inclusive no admin.
