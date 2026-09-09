# Proposal: dashboard-notifications-pwa

## Problema

O HMD está funcional de ponta a ponta (NEW→CLEANED, com anexos), mas é
"cego" entre telas: o NIR não fica sabendo que a resposta final saiu sem
abrir "Meus casos"; o agendador não sabe que um caso chegou à fila sem
polling manual; não há painel gerencial (plano §"apps/dashboard — métricas
gerenciais por tipo/unidade"); não há PWA (plano: ícone `HMD` — o dono
trocou explicitamente as letras `CHD`→`HMD`) nem manual de usuário. O dono
confirmou (respostas.md #12): **nada disso fica fora do MVP**.

## Objetivo

1. **Notificações in-app por marcos**: novo model `UserNotification`
   (apps/accounts) alimentado por signal sobre `CaseEvent` — resposta final
   publicada e reabertura por intercorrência → notificam o **criador** do
   caso; caso pronto para agendamento → notifica **todos os usuários com
   papel scheduler**. Idempotentes, sem PHI (título/texto fixos + id do
   caso), sino na navbar com contagem de não lidas.
2. **Lista e leitura**: página de notificações com janela de visibilidade
   (não lidas + lidas ≤48h), abrir marca como lida e redireciona ao caso
   **pelo papel ativo** (nir/doctor/scheduler), marcar todas como lidas.
3. **Dashboard gerencial**: novo app `apps/dashboard` com métricas por
   período (hoje/7d/30d/tudo) — resumo (total/agendados/negados/em
   andamento/encerrados), quebra por **tipo de procedimento** (decisões) e
   por **unidade** (1/2), tempo médio até a decisão médica. **Zero-PHI**
   (apenas contagens/labels; fontes imutáveis: `payload.source` do evento
   de resposta final, `CaseProcedure.decision`, `scheduled_unit`).
4. **PWA**: `manifest.json` (nome/short_name `HMD`), service worker
   adaptado do ats-web (cache de estáticos + network-first com bypass de
   rotas PDF), ícones SVG+PNG base/maskable com as letras **HMD** gerados
   por script commitado, registro em `base.html`.
5. **Manual de usuário**: página por papel (NIR/médico/agendador +
   visão geral do ciclo), link na navbar.

## Escopo

**Inclui**: `UserNotification` (migration nova em accounts) + services +
signal `CaseEvent.post_save` + context processor (badge) + views/urls
(lista/abrir/marcar-todas/contagem JSON) + navbar; app `apps/dashboard`
(services de métricas puros + view/template + navbar); manifest/sw.js/
ícones/script `scripts/generate_pwa_icons.py` (PyMuPDF, sem dependência
nova) + wiring `base.html`; `templates/accounts/manual.html` + rota +
navbar; spec `case-management` **MODIFIED** (mensagens `system` continuam
projeção pura — notificações derivam de eventos, não de mensagens).

**Não inclui**: notificações push (Web Push/FCM — fora; só in-app);
menções `@` em comunicações (modelo do ats-web não se aplica ao HMD);
polling automático do badge (contagem server-rendered por página +
endpoint JSON p/ uso futuro); PDF do manual (HTML imprimível; script de
PDF fica como follow-up); e-mail; alterações em FSM/services existentes.

## Sucesso

- Resposta final publicada → o NIR criador vê badge+1 sem recarregar
  fluxo manual; abrir leva ao caso certo pelo papel ativo; sinal duplicado
  não duplica notificação.
- Caso em `SCHEDULER_REQUESTED` → todos os schedulers notificados.
- Painel do período mostra contagens coerentes com casos de fixture, com
  quebra por tipo/unidade e **nenhum dado de paciente** na página.
- App instalável no celular (manifest+SW+ícone HMD) e manual acessível na
  navbar.

## Capabilities

- `dashboard` (nova), `notifications` (nova), `pwa-manual` (nova) — specs
  ADDED.
- `case-management` (**MODIFIED**) — requisito de comunicações esclarecido:
  notificações derivam de eventos da FSM, não de mensagens `system`.
