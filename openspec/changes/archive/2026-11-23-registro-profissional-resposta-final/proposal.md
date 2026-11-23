# Proposal

## Why

O SUREM exige que o nome e o CRM de quem autorizou/aceitou (ou recusou) um
exame sejam informados no retorno do caso. Hoje o HMD carimba o médico
decisor (`Case.doctor_decided_by`) e o modelo `User` já possui o par opcional
`professional_council`/`professional_council_number` (desde o bootstrap,
clone do padrão ats-web), porém:

1. os campos são **difíceis de provisionar e inertes no fluxo**: não estão
   expostos no Django admin (não há como preencher o CRM operacionalmente) e
   `professional_registration_display` só é consumida hoje na tela de perfil
   (`templates/accounts/profile.html`) — nenhuma composição de mensagem a usa;
2. as **mensagens finais ao NIR** — confirmação de agendamento (unidades 1 e
   2, `apps/scheduler/services.py`) e resposta final da negativa médica
   (`apps/cases/closure.py`) — não carregam identificação do médico, e é esse
   texto que o NIR repassa ao SUREM.

## What Changes

- **Provisionamento**: fieldset «Profissional» no `UserAdmin` do Django admin
  expondo `professional_council` + `professional_council_number` (mesmo molde
  do ats-web) — sem nova UI self-service.
- **Linha de crédito médico** nas respostas finais ao NIR, composta na
  postagem como última linha do corpo da mensagem:
  `Médico responsável: {nome} — {CRM NNNNN}`.
  - Fonte única: `Case.doctor_decided_by` (carimbado no atomic da decisão).
  - Sem registro preenchido → apenas o nome (degradação graciosa).
  - `doctor_decided_by` nulo → linha omitida (edge teórico: FK `SET_NULL`;
    política do dono é desabilitar, não deletar usuários).
- Mensagens afetadas: confirmação unidade 1, confirmação unidade 2 e
  resposta final da negativa médica. **Não afetadas** (decisão do dono):
  negação de agendamento, intercorrência, previews de notificação.
- Renderização: `|linebreaksbr` no `communication.body` nas 3 superfícies da
  thread (detalhe NIR, detalhe histórico NIR, detalhe do agendador) para que
  a linha (e os itens da negativa, já multilinha hoje) separem visualmente.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `account-access`: requisito «Registro de conselho profissional opcional e
  consistente» passa a exigir a superfície de provisionamento no Django
  admin (fieldset com o par de campos).
- `scheduling`: requisito «Confirmação de agendamento com unidade e resposta
  final» passa a compor a linha do médico responsável nas respostas finais
  (unidades 1 e 2).
- `case-closure`: requisito «Resposta final de negativa médica» passa a
  compor a linha do médico responsável na resposta publicada.

## Non-goals

- Novo campo `professional_registration_number` no modelo — o par existente
  cobre a necessidade sem duplicação (decisão do dono).
- Exigir CRM obrigatório para papel médico (endurecimento) — degradação
  graciosa por ora.
- Exibição do registro em cards/detalhes de caso além da mensagem (render-time,
  padrão ats-web `doctor_display`) — backlog; a tela de perfil já o exibe
  hoje e passa a mostrá-lo preenchido como efeito do provisionamento do S1.
- Crédito nas mensagens de negação de agendamento e intercorrência.
- Alteração de previews/títulos de notificação in-app (permanecem fixos).
- Migração de dados (nenhuma migration).
