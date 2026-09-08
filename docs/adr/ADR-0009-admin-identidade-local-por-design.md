# ADR-0009: Admin local por design — identidade permanente, sem flag

## Status

Accepted

## Contexto

O admin do HMD usa a credencial do Active Directory **exclusivamente para
atividades assistenciais** e não pode ter uma segunda identidade no AD (a
política institucional não permite conta administrativa duplicada). O
ADR-0003/0004 definiram a autenticação local como **break-glass de
emergência**: superusuário sem `ad_upn`, habilitado apenas pela flag de
ambiente `AD_ALLOW_LOCAL_AUTH` (default `False` em produção). Essa modelagem
não reflete a operação real: o acesso administrativo ao Django admin depende
desse superusuário local o tempo todo — ele é a **identidade permanente** do
admin do sistema, não um estado de emergência. Decisão do dono em 2026-09-08:
credencial AD é assistencial única; admin = identidade local permanente.

## Decisão

- **Admin local por design** (emenda ao break-glass do ADR-0003/0004): o
  `LocalAccountBackend` autentica **apenas superusuários sem `ad_upn`**, em
  qualquer ambiente, **sem flag de habilitação**. A regra "sem `ad_upn`"
  (hermética, blank-aware) é o que mantém o perfil local — deliberado.
- **`AD_ALLOW_LOCAL_AUTH` extinto** de settings base/dev/test, `.env.example`
  e README. Ausência da flag no ambiente é simplesmente ignorada (sem
  migração de valor).
- **Preencher `ad_upn` no superusuário continua sendo o caminho para torná-lo
  AD** (regra "sem `ad_upn`" deixa de valer; a senha local é inutilizada no
  provisionamento administrativo — ADR-0004/change 02).
- **Anti-lockout estendido ao login do Django admin**: o login do `/admin`
  ganha pre-check (bloqueado recusa cedo, sem tentativa de autenticação) e
  contagem de falhas apenas do perfil administrativo local, nos mesmos
  contadores por CPF/IP+CPF da login view (`HmdAdminSite` em `config/admin.py`).

## Alternativas Consideradas

1. **Segunda identidade do admin no AD** — rejeitada: contraria a política
   institucional (credencial assistencial única no AD — decisão do dono).
2. **Flag default-on** (manter a flag, ligada por padrão) — rejeitada:
   mantém um kill-switch que a operação nunca usou e documenta um estado de
   emergência inexistente; a ausência de flag elimina configuração divergente
   entre ambientes.
3. **Break-glass mantido** (status quo do ADR-0003/0004) — rejeitada: o
   desligamento por flag conflita com a realidade operacional — o admin local
   é usado permanentemente, não em emergência.

## Consequências

- **Superfície de força-bruta no `/admin`** é mitigada pelo anti-lockout
  estendido (limiar/janela/duração configuráveis, cache compartilhado em
  produção — mesmo contrato do change 02).
- **Desligamento rápido do admin local** pós-extinção da flag é **fora de
  banda**: `account_status=blocked` no usuário, via shell/banco — não existe
  mais chave de ambiente para isso.
- Positiva: sem configuração divergente entre ambientes; o comportamento de
  autenticação local é idêntico em dev/prod; falhas de perfil AD na rota do
  admin não contaminam os contadores locais (indisponibilidade do AD não
  conta — emenda preservada do change 02).
