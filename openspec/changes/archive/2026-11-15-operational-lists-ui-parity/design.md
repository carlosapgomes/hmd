# Design: operational-lists-ui-parity

## Context

Ver `proposal.md` — Why. O HMD já possui tokens, fontes e classes visuais do
ats-web, mas as páginas usam `col-lg-10` + `mt-4` e os partials envolvem cada
card em `list-group-item py-3`. O resultado é uma área mais estreita que o
header, padding/borda em duas camadas, conteúdo empilhado e informações
repetidas. As specs vigentes também prescrevem filtro client-side redundante no
médico, espera/recebimento em decididos e alerta de espera no NIR; os deltas
deste change substituem esses pontos.

A referência somente-leitura é `/projects/dev/ats-web`, especialmente os
templates `doctor/queue.html`, `doctor/_queue_content.html`,
`scheduler/queue.html`, `scheduler/_queue_content.html`,
`intake/my_cases.html`, `intake/_my_cases_content.html`, os `_nav.html` e
`static/css/app.css`. A prova oficial no HMD é `verify-hmd`; testes de marcadores
isolados não bastam.

## Goals / Non-Goals

**Goals:**

- Entregar páginas completas, e não apenas cards, com geometria e densidade
  próximas da referência em desktop e mobile.
- Preservar a identidade HMD e os dados específicos de seus 13 procedimentos.
- Tornar filtros, temporalidade e resultados visualmente não ambíguos.
- Escopo CSS delimitado às superfícies-alvo, sem regressão no painel/detalhes.
- Sequência de cinco slices verticais, cada um com RED → GREEN → REFACTOR,
  evidência independente e pausa para revisão humana.

**Non-Goals:**

- Paridade pixel-perfect ou cópia de campos clínicos exclusivos do CHD.
- Alterar header global/sessão/sino/manual, outras páginas ou tokens reservados.
- Mudar FSM, serviços, models, migrations, regras clínicas, guards, ordenação,
  paginação, escopo ou protocolos de partial/polling.
- Reduzir alvos interativos abaixo de 44 px para imitar os botões pequenos do
  projeto original.
- Criar abstração/componente compartilhado antes de a duplicação real exigir.

## Decisions

### D1 — Container e títulos seguem o molde da página inteira

Nas quatro páginas, remover `row justify-content-center mt-4` e `col-lg-10`.
Renderizar exatamente um `h1.page-title` por `{% block page_title %}` e deixar
conteúdo, filtros, alertas e listas no `.container` integral do `base.html`.

Títulos:

- médico pendente: `Casos aguardando decisão`;
- médico decidido: `Casos decididos` (sem prometer recorte de data);
- agendador pendente: `Solicitações de agendamento aguardando confirmação`;
- NIR: `Todos os seus encaminhamentos`.

**Alternativa rejeitada:** manter `col-lg-10`, que deixa uma segunda margem e
continua desalinhando a página do header.

### D2 — CSS com escopo explícito nas listas operacionais

Adicionar uma classe de escopo comum (por exemplo `.operational-list-page`) e,
quando necessário, classes de página nos templates. Regras novas de grid,
espaçamento e responsividade ficam sob esse escopo; não alterar globalmente
`.case-card`, `.card`, `.summary-box` ou os cards do painel. Todo vocabulário CSS
novo entra no mesmo slice do template e ganha guarda de presença/uso.

Os valores visuais já definidos pelos tokens HMD permanecem. A primária
`#0d4a8a`, o nome SAR–HMD e os ícones não são tocados.

### D3 — Uma superfície por card e CTA real

O `.case-card.card.p-3` contém diretamente uma `.row`; remover os wrappers
`list-group-item list-group-item-action py-3`. Não há segunda borda nem segundo
padding. Cada ação permitida é um `<a class="btn ...">` real; usar
`stretched-link` nesse CTA para preservar o clique amplo do card sem envelopar o
conteúdo. Card reservado por outro portador não tem link nem `stretched-link`.

Desktop:

```text
Médico/agendador
+----------------------------------------------------------------+
| col-lg-8: identificação + resumo | col-lg-4: tempo + ação     |
+----------------------------------------------------------------+

NIR
+----------------------------------------------------------------+
| col-md-4 | col-md-3 | col-md-3 | col-md-2: Ver detalhes       |
| paciente | recebido | status   |                              |
+----------------------------------------------------------------+
```

Em mobile, as regiões empilham, badges fazem wrap, não há overflow horizontal e
os CTAs mantêm pelo menos 44 px. A densidade vem da organização e da remoção de
repetição, não de reduzir o alvo de toque.

### D4 — Mapeamento de conteúdo sem importar o domínio CHD

| Superfície | Identificação/metadados | Resumo/resultado | Região direita |
| --- | --- | --- | --- |
| Médico pendente | nome, idade, ocorrência, sexo/origem quando presentes; procedimentos e subtipos | procedimentos declarados e subtipos; nenhum campo CHD inventado | `Dias em tela: N`; um `Aguardando há`; Avaliar/Continuar/Reservado |
| Médico decidido | mesma identidade compacta | cada procedimento com disposição e `doctor_decided_at` | Ver detalhes; nenhum tempo de espera |
| Agendador pendente | identidade, procedimentos e alertas de reabertura existentes | cada procedimento, subtipo e disposição médica | dias em tela; um `Aguardando há`; Agendar/Continuar/Reservado |
| NIR | nome, idade, ocorrência, sexo/origem; badges de procedimento | recebimento absoluto; status/revisão/admin-closure sem `summary-box` | Ver detalhes; nenhum tempo relativo |

Campos `Suporte`, `Fluxo` e diagnóstico do CHD não são criados para imitar a
referência. Demografia e decisões usam campos já persistidos no `Case` e nas
rows `CaseProcedure`.

### D5 — Temporalidade tem uma única função por página

- Pendente médico/agendador: somente um `.waiting-time` com `Aguardando há`;
  retirar o tempo repetido e `Recebido em` dos metadados.
- Decidido médico: nenhum `Aguardando há`, `Recebido há` ou `.waiting-time`;
  cada procedimento mostra sua data/hora de decisão.
- NIR: `Recebido em d/m/Y H:i`; nenhum tempo relativo, inclusive em `FAILED` ou
  `CLEANED`.
- `Dias em tela: N` substitui o badge compacto `N d em tela` nas abas pendentes
  modificadas; a aba `Processados` do agendador está fora da mudança visual.

### D6 — Médico: um filtro server-side de subtipo + busca client-side

Manter `?subtype=` e `_accessible_cases` como única filtragem por subtipo. O
seletor existente no server é o único controle desse conceito; remover o
segundo `<select data-doctor-exam-filter>`, a fileira `Pendentes por subtipo` e
o código JS exclusivamente dedicado a esse filtro.

Em `aguardando`, as opções usam `pending_counts` já calculado e exibem contagens;
em `decididos`, as mesmas opções aparecem sem contagens de pendentes. A busca
continua client-side sobre os cards da página e é reaplicada após o swap. O JS
não pode mais depender da presença de `typeSelect` para inicializar.

**Alternativa rejeitada:** esconder apenas visualmente um dos controles, que
deixaria dois mecanismos divergentes e uma funcionalidade oculta.

### D7 — Agendador: filtro server-side opcional por procedimento

Adicionar `procedure_type` opcional à aba `aguardando`, validado contra
`PROCEDURE_PROFILES`. Valor desconhecido equivale a vazio. O filtro usa rows
`declared_by_nir=True`, `distinct()` quando necessário e é aplicado antes da
ordenação/paginação. Não há filtro por unidade nem alteração nos contadores das
abas.

Preservar o parâmetro no formulário, `filter_qs`, links de paginação e `hx-get`
do partial. A busca por nome/ocorrência segue client-side. A aba `processados`
não recebe o novo filtro e conserva seu comportamento visual atual.

**Alternativa rejeitada:** filtro client-side apenas nos 20 cards da página, que
daria contagem/resultado incompletos sem informar essa limitação ao usuário.

### D8 — Decisões são projetadas das rows já pré-carregadas

Ampliar `_queue_item` do médico com `patient_gender`, `origin_unit`, disposição,
rótulo da disposição e data/hora por procedimento; ampliar o do agendador com os
campos equivalentes necessários. Usar `DoctorDisposition.choices` para rótulos e
`doctor_decided_at` da própria row. Não consultar `decision_event` nem events por
card e não agregar decisões mistas.

Os dados vêm do `prefetch_related("procedures")` existente. Os testes de query
devem continuar demonstrando ausência de N+1 na página e no partial.

### D9 — NIR: lista neutra, compacta e sem navegação duplicada

- Retirar o CTA `Enviar relatório` do topo e as pills internas
  `Ativos/Encerrados`; o `_nav` do header passa a ser a única navegação entre
  essas visões.
- Acrescentar `filter_qs` aos links do `_nav` quando estiver em meus casos,
  preservando filtros ao alternar `tab`; demais páginas não herdam query vazia.
- Substituir o `alert-warning` de contagem por cabeçalho neutro da lista com
  `Meus Casos` + badge do total filtrado e nota de atualização.
- Envolver a lista em um card `p-4`, como a referência. Cada item usa quatro
  regiões `4/3/3/2`; retirar `summary-box` e `waiting-time`.
- Manter badges de falha/revisão, sufixo de desambiguação e resultado/motivo de
  encerramento administrativo como informações compactas.
- Estado vazio centralizado: com filtros, `Limpar filtros`; sem filtros, texto
  da aba e `Novo Encaminhamento` quando aplicável.

### D10 — Alertas e estados vazios não competem com a lista

Médico pendente mantém `alert-warning` de contagem; quando vazio, `alert-info`.
Agendador pendente usa `alert-info` tanto para contagem quanto para vazio.
Decididos não ganha alerta de espera; seu vazio é `alert-info`. Nenhum desses
estados mostra um segundo card vazio com a mesma mensagem. O NIR não usa alerta
para contagem normal.

### D11 — Navegação operacional com um único mecanismo de contador

Portar/adaptar as regras `.app-header .app-nav`, `.app-nav .nav-link` e
`.nav-count-badge*` da referência para os tokens HMD. A aba ativa usa fundo
branco e texto na primária HMD; hover/focus continuam visíveis. Pendentes do
médico/agendador usa contador de ação; demais contadores são neutros; NIR usa
contadores neutros.

Os contadores passam a ser renderizados exclusivamente pelo mecanismo
`data-count` + pseudo-elemento. Remover os `<span class="badge">` inline dos
links para evitar dupla contagem quando o CSS for introduzido. Zero permanece
oculto. Rotas, `aria-current`, labels (`Decididos`, não `Decididos Hoje`) e
contagens não mudam. O bloco é aplicado pelos partials `_nav` dos três papéis;
`base.html`, sino e navegação do painel não são alterados.

### D12 — Aceitação por estrutura + medidas + comportamento

Cada slice deve possuir três camadas de evidência:

1. **RED automatizado:** testes focados falham antes por estrutura/ausência do
   comportamento-alvo. Exemplos: duas `.waiting-time`, presença de
   `list-group-item`, seletor redundante, decisão sem disposição/horário ou pills
   internas ainda presentes.
2. **Contratos após GREEN:** testes verificam estrutura (`.case-card > .row`,
   regiões 8/4 ou 4/3/3/2, CTA real, contagem de tempo), conteúdo, filtros,
   locks, URLs, partial e queries; testes antigos que conflitam com o novo
   contrato são atualizados deliberadamente, nunca apenas apagados.
3. **Browser:** `verify-hmd` em 1280 px e 390 px, com PNG + snapshot + JSON.
   Medidas computadas comprovam alinhamento do container ao header, regiões no
   desktop, empilhamento/mobile, ação à direita/full-width conforme a página,
   alvo ≥44 px e `scrollWidth <= clientWidth`. Capturas do HMD e da referência
   no mesmo viewport devem ser usadas quando a instância de referência estiver
   disponível; diferenças de dados impedem exigir pixel-perfect.

A jornada `fila-medica-decididos` e seu driver são adicionados porque o mapa
atual cobre apenas pendentes. As jornadas existentes de médico, agendador, NIR e
header são atualizadas para os novos contratos. Não se declara paridade apenas
pela presença das classes.

### D13 — Execução serial com revisão independente

O implementador principal executa um slice por vez e para para revisão humana.
Um subagent fresh pode revisar evidências e blast radius após cada slice, sem
editar; não há workers paralelos modificando CSS/templates. O worker de um slice
não atualiza `tasks.md`, não commita/arquiva e não inicia o próximo; a
orquestração principal faz o fechamento após revisão.

## Risks / Trade-offs

- [CSS compartilhado afetar painel/detalhe/aba processada] → Escopo D2, testes de
  regressão e capturas das superfícies vizinhas; nenhuma regra global ampla.
- [Remover o filtro client do médico quebrar inicialização ou polling] → RED de
  busca sem seletor, jornada com busca ativa durante o swap e testes de URL.
- [Filtro do agendador produzir resultado parcial ou N+1] → Filtro antes do
  paginator, testes de fora-da-página/partial e igualdade de query count.
- [Cards decididos esconderem decisões mistas] → Cenário explícito aprovado +
  negado, badges por row e nenhuma agregação case-level.
- [`stretched-link` tornar card bloqueado clicável] → Teste de ausência de link
  para reserva de outro portador e jornada de locks.
- [Retirar pills do NIR perder filtros na troca de aba] → Cenário de ida e volta
  com `filter_qs` no header e teste de partial.
- [Referência e HMD terem dados diferentes] → Comparar geometria/hierarquia e
  comportamento; não exigir igualdade de texto clínico ou screenshot idêntico.
- [Botões de 44 px ficarem menos compactos que os 36 px da referência] → Manter o
  requisito de acessibilidade do `ui-shell`; compensar com layout e menos
  repetição, sem reduzir o alvo interativo.

## Migration Plan

Sem migration, dependência, mudança de schema ou deploy especial. Cada slice é
independentemente revertível por commit. Ordem:

1. pendentes do médico;
2. pendentes do agendador;
3. decididos do médico;
4. meus casos do NIR;
5. navegação operacional + regressão visual das quatro páginas.

Rollback por slice revertendo templates/CSS/JS e projeções correspondentes. Não
há dados novos a desfazer. `PROJECT_CONTEXT.md` só é atualizado no fechamento do
change, antes do archive, após gate e evidências completos.
