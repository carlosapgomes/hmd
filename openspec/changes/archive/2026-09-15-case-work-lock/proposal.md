# Proposal: case-work-lock

## Problema

O fluxo de trabalho de médico e agendador no HMD hoje:

1. **Quebra o fluxo de leitura**: a fila leva ao `case_detail` (só leitura), o
   botão [Registrar decisão] está no **primeiro card** (topo) e navega para uma
   **página separada** com apenas o formulário (sem sumário, recomendação ou
   alertas na tela). O médico lê a recomendação lá embaixo, volta ao topo para
   achar o botão, e decide "de memória".
2. **Sem reserva de caso**: qualquer dois médicos/agendadores podem abrir o
   mesmo caso simultaneamente (a concorrência só é resolvida no POST, com
   mensagem de corrida para quem chega por último). Com equipes remotas e dois
   agendadores em unidades diferentes, isso gera trabalho duplicado de leitura
   e disputa silenciosa.

O ATS Web (mesma equipe, em produção) resolve os dois problemas com um desenho
validado: **página de trabalho única** (leitura completa + formulário no fim +
âncora de atalho + «Sair sem decidir») e **reserva de caso (work lock)** —
claim atômico na abertura, lease renovável por heartbeat enquanto há atividade,
expiração por inatividade, liberação imediata ao sair sem decidir ou ao decidir.

## Objetivo

Replicar no HMD o desenho do ATS Web para os dois papéis:

- **Página de trabalho única** para a decisão médica: a fila leva direto à
  página com toda a leitura (identificação → procedimentos → sumário →
  estrutura → documentos/anexos/prior-case → requisitos → recomendação →
  alertas) e o formulário como **último bloco**, com âncora
  «Ir para o formulário de decisão» no topo e botões
  [Registrar decisão] + [Sair sem decidir] lado a lado.
- **Reserva de caso** ao abrir a página de trabalho: caso reservado para o
  portador até decidir, sair sem decidir (liberação imediata) ou expirar por
  inatividade (lease renovada por heartbeat com detecção de atividade).
- **Fila honesta**: cards mostram 🔒 Reservado (com o nome do portador),
  ▶ Continuar avaliação (própria reserva) ou a ação livre (Avaliar/Confirmar);
  reservas vencidas são expiradas de forma lazy antes de montar a fila.
- **Agendador espelhado**: mesma semântica na confirmação/negação de
  agendamento.

## Escopo

- `apps/cases/locks.py`: lease por contexto (`doctor_decision` →
  `CASE_LOCK_LEASE_SECONDS_DOCTOR`, default 3600s como no ATS), helpers de
  exibição (`compute_lock_display`) e expiração filtrada por status
  (`expire_stale_locks_for_statuses`).
- `apps/doctor/views.py` + templates: página única de decisão com claim,
  token no formulário, `assert` + release no POST, fila com estado de reserva.
- `apps/scheduler/views.py` + templates: espelho para confirmação/negação.
- `static/js/work_lock.js` (adaptado do ATS Web): heartbeat de renovação com
  tracking de atividade, aviso de falha, submit guard e release best-effort em
  pagehide/clique em link.
- View server-side «sair sem decidir» (`case_leave`) com liberação garantida
  sem depender de JavaScript.
- Sem mudanças de modelo/migration (os seis campos de lock já existem em
  `Case`; workers já os usam com contexto `worker`).

## Sucesso

- Médico abre da fila → lê tudo em fluxo único → decide no fim da página, sem
  rolar de volta; ou sai sem decidir e o caso volta disponível na hora.
- Dois médicos: o segundo vê «Reservado por Dr. X» no card e é barrado na
  abertura com mensagem clara; nunca trava em erro 500.
- Médico inativo além da grace → renovação para → lease vence → caso volta à
  fila sozinho.
- Agendador das unidades 1 e 2: mesmo comportamento, sem interferência entre
  si (reserva por caso).
- Suíte completa verde; ruff/mypy limpos; specs sincronizadas.

## Fora de escopo

- Indicador «sendo lido por» em superfícies sem reserva (painel, NIR).
- Reserva para o NIR (o fluxo do NIR não tem disputa de decisão).
- Lock pessimista na leitura do painel/ATS-parity (leitura não muta).
- Mudanças no mecanismo de lock dos workers (contexto `worker` permanece).
