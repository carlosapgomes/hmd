# Proposal

## Why

Desde o change `ad-kerberos-authentication` a senha dos usuários pertence ao
Active Directory do hospital (ADR-0003), mas a página de perfil — aberta pelo
avatar da navbar — ainda exibe um card «Alterar senha» com formulário
processado em `POST /profile/`. Para o usuário AD esse formulário nunca
funcionou (a senha local é inutilizável; a conferência da senha atual sempre
falha), mas a presença do formulário comunica que a troca é possível no
aplicativo. Decisão do dono (2026-11-24): a senha do AD só é trocada
pessoalmente na TI do hospital; o aplicativo não pode oferecer nem sugerir
troca de senha, e todo endpoint que permita a troca deve ser bloqueado.

## What Changes

- **Perfil só de leitura + orientação** (**BREAKING**: `POST /profile/` deixa
  de processar troca de senha e passa a responder 405):
  - card «Alterar senha» removido; dados da conta passam a ocupar a largura
    total;
  - linha de orientação no lugar do formulário (texto fixo em `design.md` D2):
    a troca é feita pessoalmente na TI do hospital;
  - `HospitalPasswordChangeForm` removida (só o perfil a usava).
- **Rotas de troca de senha do Django admin removidas** (**BREAKING**: três
  rotas herdam do Django sem o projeto as ter pedido — escopo confirmado pelo
  dono em 2026-11-24). Enumeração autoritária
  do resolver em 2026-11-24 (Django 5.2.17), nenhuma rota de
  `django.contrib.auth.urls` incluída:
  - `/admin/password_change/` (`admin:password_change`) — autoatendimento do
    usuário logado no admin; a barra do admin exibe o link «Alterar senha»
    para quem tem senha utilizável (o admin local; `LANGUAGE_CODE=pt-br`);
  - `/admin/password_change/done/` (`admin:password_change_done`) — página de
    sucesso do fluxo acima;
  - `/admin/accounts/user/<id>/password/` (`admin:auth_user_password_change`)
    — define/troca a senha de outro usuário; botão «Definir senha» no
    formulário de edição de usuário. Grava por `form.save()` direto, por cima
    do `save_model` — poderia tornar utilizável a senha local de um usuário AD,
    violando a spec `account-access`.
  - Requisições aos caminhos removidos não exibem senha nem gravam nada: as
    duas rotas de autoatendimento terminam em 404 (catch-all do admin); a rota
    por usuário é redirecionada ao índice do admin pelo padrão de
    compatibilidade do `ModelAdmin` (D3).
- **Sem links mortos no admin**: o link da barra e o botão do formulário de
  usuário saem junto com as rotas (o link da barra é um `{% url %}` — removê-lo
  é condição para o admin continuar renderizando).
- **Rotação da senha do admin local fora do aplicativo**: a identidade
  administrativa local (ADR-0009) perde o autoatendimento; a TI rota a senha
  com `manage.py changepassword`. Registrado em `README.md` e em ADR novo.
- **Fora de escopo (decisão do dono, 2026-11-24)**: os campos de senha do
  formulário de **criação** de usuário no admin (`usable_password`,
  `password1`, `password2`) permanecem — é criação da identidade local, não
  troca, e `save_model` já inutiliza a senha de usuários com `ad_upn`.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `account-access`: dois requisitos novos (ADDED) — «Perfil sem troca de
  senha» e «Sem rotas de troca de senha no aplicativo». Nenhum requisito
  existente precisa de MODIFIED: a spec nunca pinou o formulário (o requisito
  de rótulos cobre apenas a lista de papéis do perfil) e a regra «usuário AD
  não tem senha local utilizável» permanece intacta.

## Impact

- **Código (perfil)**: `apps/accounts/views.py`, `apps/accounts/forms.py`,
  `templates/accounts/profile.html`.
- **Código (admin)**: `config/admin.py`, `apps/accounts/admin.py`,
  `templates/admin/base.html` (novo — override do template do Django),
  `apps/accounts/templates/accounts/widgets/read_only_password_hash.html`
  (novo; template de widget no nível do app — ver D5).
- **Testes**: `apps/accounts/tests/test_auth_flow.py` (inversão de
  `test_profile_password_change`), dois arquivos de teste novos; harness
  `verify-hmd` (`scripts/browser/drive-session-pages.mjs`,
  `features/sessoes.md`).
- **Docs**: `README.md` (rotação da senha do admin local),
  `docs/adr/ADR-0011-*` (novo).
- **Operação**: o admin local deixa de trocar a própria senha pelo app;
  `seed_admin` em re-execuição continua sem resetar senha (preservado).
- Sem migration, sem mudança de modelo, sem dependência nova, sem mudança em
  autenticação/backends/anti-lockout.
