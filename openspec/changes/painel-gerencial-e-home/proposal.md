# Change: painel-gerencial-e-home

## Why

Ajustes de UX pedidos pelo dono a partir do uso real do piloto (2026-09-13):

1. O link **[Painel]** aparece para todo autenticado e a rota é
   `login_required` apenas — mas o painel é ferramenta gerencial: só
   `manager` e `admin` devem vê-lo/usá-lo.
2. O menu do NIR vem na ordem `[Meus Casos] [Enviar relatório]` — o caso de
   uso mais comum do NIR é **enviar relatório**, que deve vir primeiro.
3. A home pós-login é um **placeholder** ("Bem-vindo… as filas do seu papel
   substituirão esta tela") para TODOS os papéis. Cada papel deve cair direto
   na sua área de trabalho (o próprio template anuncia a substituição).

## What Changes

- **Painel exclusivo manager/admin**: link da navbar gated por papel ativo
  `manager`/`admin` E rota `dashboard:home` protegida por
  `@role_required("manager", "admin")` (403 para os demais — menu escondido
  não é controle de acesso; mesmo padrão das filas doctor/scheduler).
- **Home por papel ativo** (`home_view` vira dispatcher de redirect):
  `nir` → `intake:home` (Enviar relatório) · `doctor` → `doctor:queue` ·
  `scheduler` → `scheduler:queue` · `manager`/`admin` → `dashboard:home` ·
  sem papel ativo válido → placeholder atual (reinício de sessão estranho).
  Bônus: o redirect de notificações (`resolve_notification_redirect_url`,
  que manda manager/admin para `home`) passa a aterrissar no painel.
- **Menu NIR**: `[Enviar relatório] [Meus casos]` (swap; Manual/Painel seguem
  as regras globais — Painel some do NIR pela regra nova).

## Impact

- **Specs**: `dashboard` MODIFIED ("Acesso ao painel": manager/admin, 403
  demais) + `account-access` ADDED ("Home por papel ativo", cenários por
  papel).
- **Código**: `apps/dashboard/views.py` (guard), `templates/base.html`
  (nav), `apps/accounts/views.py` (`home_view` dispatcher),
  `templates/accounts/home.html` (texto do placeholder perde a promessa
  "próximas versões"), testes.
- **Risco**: baixo — redirects pós-login; o placeholder permanece como
  fallback; nada de FSM/migrations.
