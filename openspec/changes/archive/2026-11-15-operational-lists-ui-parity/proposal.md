# Proposal: operational-lists-ui-parity

## Why

A rodada anterior de paridade trouxe classes e interações do ats-web, mas as
quatro listas operacionais ainda divergem da referência em largura, densidade,
hierarquia, filtros, alertas e organização dos cards. A aceitação por marcadores
de classe não comprovou paridade geométrica; esta rodada corrige as páginas
completas, com evidência visual desktop/mobile e sem alterar as regras de
regulação.

## What Changes

- **Pendentes do médico:** alinhar container, título, filtro, alerta, cards e
  estado vazio ao padrão ats-web; usar um único filtro de subtipo server-side
  (com contagens nas opções) + busca client-side; cards em conteúdo 8/12 e
  tempo/ação 4/12, sem espera duplicada, com dias em tela e locks preservados.
- **Pendentes do agendador:** aplicar o mesmo modelo de página e card, com
  alerta informativo, filtro server-side por tipo de procedimento + busca
  client-side, decisões médicas por procedimento e ações de agendamento/locks.
- **Decididos do médico:** substituir o card de espera por um card orientado ao
  resultado, com disposição e horário por procedimento, sem agregar decisões
  mistas e sem colorir toda decisão como aprovação; CTA «Ver detalhes».
- **Meus casos do NIR:** alinhar página, filtros, cabeçalho/contador da lista,
  cards compactos em quatro colunas, estado vazio e mobile; remover a navegação
  interna duplicada e o CTA redundante, mantendo as URLs e a navegação do header.
- **Navegação operacional:** harmonizar pills ativas e contadores das áreas NIR,
  médico e agendador aos tokens HMD, sem alterar rotas, papéis ou contagens.
- Remover o wrapper `list-group-item` e o espaçamento/borda em duas camadas nos
  cards modificados, substituindo-o por ações reais; preservar o clique amplo
  por `stretched-link` quando houver ação permitida.
- Validar cada fatia com TDD, `verify-hmd` em desktop/mobile e quality gate;
  aceitar geometria e organização observáveis, não apenas presença de classes.

Não há mudança de FSM, ordenação, guards, escopo por criador/subtipo, paginação,
polling, regras de decisão/agendamento, persistência, cores reservadas ou
identidade SAR–HMD. Header global, sessão, sino, manual, detalhe, upload e
painel ficam fora do escopo.

## Capabilities

### New Capabilities

(nenhuma)

### Modified Capabilities

- `doctor-decision`: apresentação completa das abas da fila, filtro de subtipo
  sem seletor client-side redundante e cards de pendentes/decididos com
  hierarquia e temporalidade apropriadas.
- `scheduling`: apresentação completa da aba pendente, filtro por tipo de
  procedimento e card operacional com decisões médicas e ações de reserva.
- `intake-nir`: apresentação completa de meus casos, navegação sem duplicação,
  contagem neutra e cards compactos sem alerta/espera redundantes.

## Impact

- **Templates:** filas e partials de `doctor/`, `scheduler/` e `intake/`, além
  dos partials de navegação dos três papéis.
- **Apresentação:** `static/css/app.css` e JS de busca/filtro das filas.
- **Contexto de apresentação:** `apps/doctor/views.py` e
  `apps/scheduler/views.py` somente para projecção de campos e filtros das
  listas; sem serviços, models, migrations ou transições.
- **Testes:** testes de filas, navegação, partials, queries e preservação de
  comportamento; extensões limitadas das jornadas `verify-hmd` correspondentes.
- **Referência somente-leitura:** templates e CSS de `/projects/dev/ats-web`.
- **Compatibilidade:** rotas e parâmetros existentes continuam válidos; o novo
  `procedure_type` do agendador é opcional. Nenhuma dependência nova, mudança de
  API pública ou alteração de regra clínica/operacional.
