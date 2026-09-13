# Design — painel-gerencial-e-home

## D1 — Painel: gate NA ROTA, não só no menu

`@role_required("manager", "admin")` em `dashboard:home` (decorador existente
de `apps/accounts/decorators.py`, mesmo padrão das filas doctor/scheduler:
papel ativo fora da lista → 403). O link da navbar usa a MESMA condição
(`active_role == 'manager' or 'admin'`) — UI e rota não divergem. Anônimo
segue para o login (`login_required` já cobre; `role_required` assume
autenticado — composição igual às filas).

## D2 — Home por papel: dispatcher de redirects, placeholder como fallback

`home_view` mantém `@login_required` e passa a despachar por
`request.session.get("active_role")`:

| Papel ativo | Destino |
| --- | --- |
| `nir` | `intake:home` (formulário Enviar relatório) |
| `doctor` | `doctor:queue` |
| `scheduler` | `scheduler:queue` |
| `manager` / `admin` | `dashboard:home` |
| ausente/inválido | placeholder atual |

Redirects são 302 simples (`redirect(reverse(...))`), sem flash de conteúdo.
O `ActiveRoleMiddleware` já garante que `active_role` na sessão é um papel
que o usuário possui (senão limpa/logout) — o dispatcher não revalida papéis.
Rotas de destino todas já protegidas por si (guard próprio por papel): não
há risco de a home "abrir" uma rota que o papel não pode acessar.

## D3 — Notificações continuam coerentes

`resolve_notification_redirect_url` (nir/doctor/scheduler → `case_detail`;
resto → `home`) não muda: para manager/admin o destino `home` agora
aterissa no painel — exatamente o desejado. Nenhuma edição na função.

## D4 — Menu NIR

Swap de ordem dos dois links do bloco nir (`Enviar relatório` antes de
`Meus casos`); Painel some do NIR pela regra D1 (bloco do painel passa a
exigir manager/admin). Nenhum outro bloco de nav muda.

## D5 — Placeholder

`templates/accounts/home.html` continua existindo como fallback (sem papel
ativo válido) — o texto "As filas do seu papel substituirão esta tela nas
próximas versões" é atualizado (a substituição aconteceu; o fallback orienta
trocar de papel/sair).
