# Changelog

Formato: versões com resumo por change (Keep a Changelog adaptado ao workflow
OpenSpec — cada change tem proposal/design/slices/specs arquivados em
`openspec/changes/archive/`).

## [0.1.31] — 2026-11-25

**Lista de usuários do admin com nome completo + correção de teste
flaky** — 2 changes (`admin-lista-usuarios-nome-completo`, 1 slice TDD;
`test-espera-herdada-data-local`, 1 slice só de teste), execução direta
na sessão do dono **sem revisão independente por slice** (decisão do
dono). Suíte 2861 → **2870** (+9 testes). Sem migrations e **sem seeds
novos** (passo 5 do runbook roda idempotente como segurança). **Deploy
direto.**

- **Lista de usuários do Django admin**: coluna «Nome completo» (nome e
  sobrenome, com fallback para o `username`) logo após o `username`, que
  segue a primeira coluna e o link de edição; ordenável pelo primeiro nome;
  busca também por nome e sobrenome. Motivação: incidente de 25/11 — dois
  usuários AD provisionados com `username` de nome real não conseguiram
  entrar digitando o CPF (o login é o `username`, nunca o UPN). Sem
  mudança de login, de modelo nem de autenticação.
- **Teste**: a pré-condição de
  `test_pending_card_uses_inherited_wait_in_html` (`apps/doctor`) passa a
  comparar data local — o teste falhava todos os dias entre 21:00 e 00:00
  (-03) e quebrou o gate de 25/11. Nenhuma mudança de produção.
- Operação: para o usuário AD entrar, o login é o `username` cadastrado
  (CPF, por desenho) — renomear no admin ou orientar o usuário; a coluna
  «Nome completo» é só exibição.

## [0.1.30] — 2026-11-24

**Bloqueio de troca de senha (BREAKING — senha é do AD, só na TI)** —
1 change (`bloqueio-troca-de-senha`), 2 slices TDD com revisão
independente por slice. Suíte 2838 → **2861** (+23 testes). Sem
migrations e **sem seeds novos** (passo 5 do runbook roda idempotente
como segurança; catálogo segue em 14 tipos e os seeds do `neurointerv`
estão em produção desde a v0.1.26).

- **Perfil sem troca de senha (BREAKING)**: `POST /profile/` não
  processa mais troca e responde 405; card «Alterar senha» removido
  (dados da conta em largura total) com linha de orientação fixa — a
  troca é feita pessoalmente na TI do hospital; `HospitalPasswordChangeForm`
  removida.
- **Rotas de troca de senha do Django admin removidas (BREAKING)** por
  filtragem por nome (override de `admin/base.html` sem o link, com
  teste anti-drift; formulário de edição de usuário sem botão de
  senha).
- A senha do admin local passa a ser rotacionada pela TI via
  `manage.py changepassword` (ADR-0011).

## [0.1.29] — 2026-11-24

**Reapresentação de casos e filas (deduplicação de reenvios)** — 1
change (`reapresentacao-casos-filas`), 5 slices TDD com revisão
independente por slice. Suíte 2431 → **2838** (+407 testes).
**COM migrations (0016–0017):** CNS do paciente + vínculo de
reapresentação — o passo `migrate` do deploy aplica schema.
**Sem seeds novos** (passo 5 do runbook roda idempotente como
segurança; catálogo segue em 14 tipos e os seeds do `neurointerv`
estão em produção desde a v0.1.26).

- **Identificação de reapresentações** por nome normalizado + nº de
  regulação, CNS (bloco SESAB completo) ou unidade de origem — sempre
  do mesmo procedimento; evidência forte resolve automático, o resto
  vai a conferência do NIR (duas respostas, com os dois PDFs), sem
  bloqueio por falta de resposta.
- **Fila médica**: substituição só com o novo integralmente
  processado, espera acumulada preservada sem reescrever `created_at`;
  **agendamento**: original/aprovação/andamento preservados, novo
  encerrado vinculado após LLM1 (sem LLM2), PDFs/anexos separados
  inclusive no histórico `CLEANED`.
- Negativa médica anterior informa o novo ciclo em janela única de 15
  dias; encerrados por reapresentação saem de «Em andamento» e da
  tabela por procedimento (volume de envios e auditoria preservados);
  proteção temporária contra minimização/purga de documentos ligados a
  caso ativo.

## [0.1.28] — 2026-11-23

**CRM do médico nas respostas finais ao NIR (exigência SUREM)** — 1
change (`registro-profissional-resposta-final`), 2 slices TDD com
revisão independente por slice. Suíte 2411 → **2431** (+20 testes).
Sem migrations e **sem seeds novos** (passo 5 do runbook roda
idempotente como segurança; catálogo segue em 14 tipos e os seeds do
`neurointerv` estão em produção desde a v0.1.26).

- **Provisionamento**: fieldset «Profissional» no `UserAdmin` expondo
  `professional_council` + `professional_council_number` (molde
  ats-web) — sem UI self-service; campos já existiam no modelo desde
o bootstrap.
- **Linha de crédito médico** como última linha das respostas finais
  ao NIR: «Médico responsável: nome — CRM N» nas confirmações de
  agendamento (unidades 1 e 2) e na negativa médica, com degradação
  graciosa quando o CRM não está preenchido; `linebreaksbr` na thread
  nas 3 superfícies; manual atualizado.

## [0.1.27] — 2026-11-22

**Visibilidade por equipe nos 3 papéis + endurecimento de testes** —
2 changes (`visibilidade-por-equipe`, 7 slices + `p2-hardening`,
2 slices test-only), com revisão independente por slice. Suíte 2377 →
**2411** (+34 testes). Sem migrations e **sem seeds novos** (os seeds do
`neurointerv` já estão em produção desde a v0.1.26; o passo 5 do
runbook roda idempotente como segurança).

- **NIR em escopo de equipe**: «Meus Casos» vira «Casos em
  Processamento» — todos os casos ativos de qualquer criador; detalhe
  ativo, PDFs/anexos, ciência, liberação/reenvio do gate e confirmação
  de procedimento acessíveis a qualquer `nir` ativo (views + serviços,
  com re-check transacional de papel; `PermissionError → 403`).
  Motivação: incidente real em produção (conta break-glass via lista
  vazia interpretada como casos "sumidos").
- **Abas «Hoje» por equipe/especialidade**: «Decididos Hoje» (qualquer
  decisor do subtipo, decisor visível no card) e «Processados Hoje"
  (qualquer decisor); contadores do header no mesmo escopo.
- **PDF do agendador por decisão**: gate passa a ser por decisão de
  agendamento + estado (confirmação e negação), não por decisor;
  negativa médica direta segue sem acesso.
- **p2-hardening** (test-only): negações alheias no partial, gate em
  `AWAITING_NIR_ACK`, re-check convergido a helper, manual preciso.

## [0.1.26] — 2026-11-22

**Catálogo com 14 tipos: Neurointervenção (embolização de aneurisma/malformação
vascular intracraniana)** — 1 change (`add-neurointervencao-procedure`), 2 slices
TDD com revisão independente por slice. Suíte 2369 → **2377** (+8 testes;
no branch de origem: 2331 → 2339).
**Sem migrations, MAS com seeds — passo 5 do runbook de deploy OBRIGATÓRIO**
(cria `proc.neurointerv.llm1/llm2.user`; a 1ª chamada de prompt do tipo novo
falha se o seed for pulado).

- **Novo tipo `neurointerv`** (decisão do dono 2026-11-22): critérios da seção
  S2 por referência (a mais restritiva — angioplastias carótidas/membros/
  coronariana), subtipo doctor `neuro` (médico neuro passa a ver 2 tipos),
  suporte anestésico. `EXPECTED_PROCEDURE_COUNT` 13→14; ordem canônica logo
  após `art_cerebral`. Nenhuma migration, nenhum bloco de schema novo (base
  comum), policy S2 automática por mapeamento de seção.
- **Fechamento da lacuna declarativa**: pedidos de embolização agora podem ser
  declarados pelo NIR (select do upload e da revisão de procedimento),
  roteados ao subtipo `neuro` e detectáveis pelo LLM1 (detecção livre sobre o
  catálogo). Texto do card de revisão: "um dos 14 tipos do catálogo".
- **Higiene pós-catálogo-14**: varredura de literais obsoletos (grep 38→8;
  remanescentes apenas históricos: ADRs arquivados + nota do change 01/P2),
  help do `seed_prompts` (31 templates), contagens em docstrings/testes.
- **Runbook**: passo 5 (migrações+seeds) agora documenta ser obrigatório em
  releases "sem migrations, MAS com seeds"; convenção do changelog distingue
  "sem migrations e sem seeds (deploy direto)".

## [0.1.25] — 2026-11-22

**Badge de transporte do relatório (extração determinística do
`TRANSPORTE:`)** — 1 change (`transport-badge`), 3 slices TDD com
revisão independente por slice. Suíte 2331 → **2369** (+38 testes).
**COM migration (0015):** novo campo `Case.transport_type` — o passo
`migrate` do deploy aplica schema.

- Extração determinística do label `TRANSPORTE:` no worker de PDF
  (regex linha-âncorado sobre o texto já limpo, mesmo write do
  `extracted_text` — verificado em 9/9 PDFs do corpus real);
  normalização tolerante (`Ambulância Básica`/`BÁSICA` → `basic`,
  `UTI Móvel` → `mobile_icu`); desconhecido/ausente → vazio (nunca
  inventado).
- Badge `🚑` nos cards das três filas (meus casos, fila médica,
  agendador) — `UTI Móvel` com ênfase visual de gravidade, básica
  neutra; linha de transporte no card de identificação do detalhe
  médico.
- Sem mudanças no pipeline LLM (prompts, schemas, policy e fronteira de
  anonimização intactos; o valor não é PII).

## [0.1.24] — 2026-11-22

**Reenvio corrigido por qualquer NIR + consolidação do nav (paridade
ats-web)** — 1 change (`reenvio-corrigido-qualquer-nir`), 2 slices TDD
com revisão independente por slice. Suíte 2314 → **2331** (+17 testes).
Sem migrations (deploy direto).

- **Reenvio corrigido por qualquer papel `nir` ativo** (admin incluso),
  não só o criador: guarda por papel em view e serviço, ação «Reenviar
  corrigido» no detalhe histórico (criador e não-criador, mesma porta);
  o reenviador torna-se o criador do novo caso (notificações e ciência
  do novo ciclo). Invariantes preservadas: 1 PDF + 1 tipo, motivo
  obrigatório, original íntegro com supersedição, desambiguação de nº
  duplicado.
- **Consolidação do nav (opção A)**: «Meus Casos» lista só ativos
  (`?tab=closed` legado cai na default segura); nav do NIR em 3 itens —
  «Casos Encerrados» passa a ser a busca global (badge de encerrados
  removido, manual atualizado); selo de encerramento administrativo no
  detalhe histórico.
- Acessos do criador preservados por outras vias (redirect pós-ciência,
  notificações, links condicionais sem 404 entre criadores).

## [0.1.23] — 2026-11-21

**Paridade operacional: sumário completo ao agendador, busca histórica,
intercorrência pós-encerramento, abas «Hoje» por decisor** — 2 changes
(`paridade-ats-filas-historico-intercorrencia`, 5 slices +
`tests-hardening-paridade-ats`, test-only), com revisão independente por
slice. Suíte 2219 → **2314** (+95 testes). **COM migrations
(0012–0014):** intercorrência pós-encerramento + carimbos de decisão com
backfill auditável — o passo `migrate` do deploy aplica schema.

- **Sumário completo ao agendador**: detalhe do agendador/admin exibe o
  sumário clínico completo re-identificado (antes: primeira linha);
  estrutura extraída/alertas/sugestão seguem fora do escopo — paridade
  ats-web.
- **Busca histórica (NIR e agendador)**: páginas de busca server-side por
  nº de ocorrência ou nome sobre casos `CLEANED` e intercorrências
  ativas (escopo histórico com guarda própria; «meus casos» intacto),
  cap de 50 resultados, detalhe histórico read-only.
- **Intercorrência pós-encerramento**: o NIR pode reabrir caso `CLEANED`
  para re-agendamento (FSM, marcador de disposição, issue única,
  fan-out de notificação); ADR-0010 emendada (evidência canônica de
  disposição + inelegibilidade fail-closed da reabertura).
- **Abas «Hoje» por decisor**: «Decididos Hoje»/«Processados Hoje» com
  janela do dia local (carimbos `doctor_decided_*`/`scheduling_decided_*`
  com backfill auditável); spec nova `post-closure-issue` (16→17 specs).

## [0.1.22] — 2026-11-21

**Trilha de eventos em todos os perfis (paridade com o painel)** — 1
change (`trilha-eventos-todos-perfis`), 3 slices TDD com revisão
independente por slice. Suíte 2210 → **2219** (+9 testes). Sem
migrations (deploy direto).

- A trilha de eventos (`CaseEvent`, append-only, ator/papel/timestamp)
  deixa de ser exclusiva do painel: NIR, médico e agendador passam a ver
  a mesma trilha no detalhe do caso — quem agiu, não só o quê e quando.
- Novo módulo de domínio `apps/cases/event_trail.py` (rótulos legíveis
  45/45 tipos, badges por papel, `role_label` em português, linhas de
  detalhe) migrado do dashboard sem quebrar a dependência unidirecional;
  serviço puro com `select_related("actor")`, sem N+1.
- Partial compartilhado `cases/_event_trail.html` (card collapsible
  «Trilha de eventos», fechado por padrão, markup idêntico ao do painel);
  guards por papel preservados — cada perfil vê só os eventos do seu
  escopo.

## [0.1.21] — 2026-11-20

**Confirmação autoritativa de procedimento pelo NIR (paridade ats-web)** —
1 change (`confirm-nir-procedure-review`), 4 slices TDD com revisão
independente por slice. Suíte 2031 → **2210** (+179 testes). Sem
migrations (deploy direto).

- Nos casos enviados após a implantação, o bypass de divergência vira
  revisão explícita: o NIR escolhe **um único procedimento entre os 13**
  do catálogo com justificativa (1–500 caracteres) e confirmação de
  leitura — mesmo para manter o exame original; autorização, revisão e
  fonte validadas sob transação.
- Confirmação humana registrada como autoritativa com reprocessamento do
  **mesmo caso** desde LLM1 (perfil confirmado → policy → prior-case →
  LLM2); PDF, anexos, texto extraído, anonimização e história
  preservados; sem loop pela mesma divergência; falha no worker vira
  `FAILED` operacional com recuperação automática de enqueue (sem novo
  envio externo).
- Médico recebe resumo e análise do procedimento confirmado com aviso de
  confirmação × detecção automática (autor, data, justificativa) e
  ressalva de decisão clínica exclusiva; casos anteriores e bypasses
  antigos não são reinterpretados.

## [0.1.20] — 2026-11-18

**Retenção temporária no encerramento + auditoria do painel (ADR-0010) +
endurecimento do strip de marca d'água** — 2 changes
(`retencao-auditoria-casos-encerrados`, `watermark-strip-hardening`),
5 slices TDD com revisão independente por slice. Suíte 1958 → **2031**
(+73 testes). Sem migrations (deploy direto).

- **retencao-auditoria-casos-encerrados** — durante a fase de
  estabilização (`CASE_CLOSURE_MINIMIZATION_ENABLED=false`, default), a
  ciência do NIR e o encerramento administrativo **preservam** os 7
  campos clínicos/LLM, as rows e os arquivos físicos (corpus real para
  benchmark de anonimização/modelos); estados, eventos, guards, locks e
  notificações idênticos. Detalhe do painel ganha o card «Sumário
  apresentado ao médico» (inclusive em casos encerrados). Purga retroativa
  em duas fases (serviço + comando + trilha de auditoria).
- **watermark-strip-hardening** — remoção passa a nomear marca em linhas
  soltas e poucas repetições por linha (antes, só banda intra-linha
  cheia atravessava marcas diagonais); sweep residual assimétrico
  preserva valores legítimos de 5–6 dígitos em linha mista (CBO,
  protocolos) e telemetria da remoção no evento de extração.

## [0.1.19] — 2026-11-16

**Visualização do PDF original e dos anexos nos 4 papéis (paridade ats-web)**
— 1 change (`pdf-original-viewer-parity`), 7 slices TDD com revisão
independente por slice. Suíte 1762 → **1958** (+196 testes). Sem
migrations (deploy direto).

- Cards colapsáveis padronizados nos detalhes de caso dos 4 papéis (NIR,
  médico, agendador, supervisor/painel): `Texto Extraído do PDF` (`<pre>`
  com `Case.extracted_text`) e `Visualizar PDF Original` (embed lazy no
  desktop com botão "Abrir em nova aba"); supervisor passa a ter acesso
  ao PDF (rotas novas, guards manager/admin); agendador sob o gate
  pós-decisão vigente, inalterado.
- Viewer mobile dedicado por papel (PDF.js vendored, nav sticky com
  `← Voltar`, `?next=` resolvido com helper seguro); cada `CaseDocument`
  ganha seu sub-collapse lazy (HMD tem 1–N documentos por caso).
- Anexos de evidência com arquivo servido e visualizável (embed lazy para
  PDF, imagem lazy para JPEG/PNG) nos cards de anexo do NIR, médico e
  painel — rotas de serve por papel com os guards já vigentes.
- Infra compartilhada: helper `next` seguro, template mobile, lazy-embed
  e `SAMEORIGIN` nos serves.

## [0.1.18] — 2026-11-15

**Listas operacionais na paridade geométrica + ícone no molde do CHD** —
2 changes (`operational-lists-ui-parity`, `pwa-icon-simplify`), 6 slices
TDD com revisão independente por slice. Suíte 1707 → **1762** (+55

testes). Sem migrations (deploy direto).

- **operational-lists-ui-parity** — as quatro listas operacionais ficam com a
  geometria e a densidade do ats-web, com evidência visual desktop/mobile:
  pendentes do médico e do agendador no modelo de página completo (container,
  título, filtro de subtipo server-side com contagens nas opções + busca
  client-side, alerta, cards em conteúdo 8/12 × tempo/ação 4/12, dias em
  tela, locks preservados); decididos do médico vira card orientado ao
  resultado (disposição e horário por procedimento, sem agregação);
  meus casos alinhado ao padrão; navegação operacional harmonizada. Sem
  mudança em regras de regulação.
- **pwa-icon-simplify** — ícone HMD no molde do CHD: fundo plano na
  primária `#0d4a8a` + sigla "HMD" centralizada, sem a faixa teal (que não
  existe na referência); variante `-contrast` e PNGs regenerados;
  diferenciação entre os sistemas segue pela cor/sigla/nome.

## [0.1.17] — 2026-11-14

**Runbook de deploy unificado + repo neutro em infra** — 3 changes de
operação (docs/parametrização/auditoria), sem mudança funcional de
aplicação. Suíte 1705 → **1707** (+2 testes-guarda). Sem migrations
(deploy direto). **Ação exigida no host:** o compose passa a exigir
`HMD_INGRESS_IPV4` e `CSRF_TRUSTED_ORIGINS` no `.env` (fail-closed em dois
níveis: compose sem default + settings sem default).

- **Runbook de deploy** (`docs/runbook-deploy-producao.md`): topologia real
  do servidor de produção, procedimento de upgrade passo a passo,
  pós-checks, rollback, diagnósticos, housekeeping e divergências conhecidas
  — unifica README (piloto/fase 2) + spec `production-deployment` + estado
  verificado no host.
- **Parametrização de infra**: IP fixo do web na rede de ingress passa a
  `HMD_INGRESS_IPV4` (env obrigatória) — elimina o patch local de
  `ipv4_address` no host; `CSRF_TRUSTED_ORIGINS` sem default no compose
  (domínio público não versionado em repo público).
- **Auditoria de exposição**: domínio real deixa de ser default das
  settings de prod (lista vazia), host de produção neutralizado em docs e
  comentários, e dois testes-guarda novos: compose falha nomeado sem as
  envs obrigatórias; `git grep` impede domínio real e IPs internos
  (`172.30.x`, `10.17.168.x`) em arquivos vivos (histórico/CHANGELOG/
  archives ficam fora — registro imutável).

## [0.1.16] — 2026-11-14

**Paridade visual SAR–HMD (casca, filas, intake/detalhe/painel) + semântica
de lote do intake** — 4 changes (`ui-parity-foundation`,
`ui-parity-queues`, `ui-parity-intake-detail`, `intake-batch-semantics`),
15 slices TDD com revisão independente por slice. Suíte 1553 →
**1705** (+152 testes). Sem migrations (deploy direto).

- **ui-parity-foundation** — o HMD entra na família SAR: rename para
  «SAR – HMD» (manifest, títulos, ícone com a sigla + marcador geométrico
  da família, PNGs regenerados; cor primária e `short_name` preservados),
  header com logo, faixa de navegação por papel, fontes do tema
  hospitalar e CSS com tokens; harness de verificação `verify-hmd`
  (launch isolado com seed sintético, drive Playwright pinado).
- **ui-parity-queues** — filas médica e do agendador em `case-cards` com
  `summary-box`, alerta de espera (`waiting-time`), `status-badge` e abas
  com contadores (aba do médico rotulada «Decididos», fiel à query);
  busca client-side por nome/nº de ocorrência; partials htmx com
  auto-refresh a cada 20 s; «Meus casos» do NIR em cards com filtros
  server-side (nº, status, tipo) + auto-refresh. Sem mudança em regra de
  negócio, ordenação, guards ou semântica das abas.
- **ui-parity-intake-detail** — última milha da paridade: upload NIR com
  drag-and-drop e preview (arquivos, contagem, tamanho total);
  `procedure_type` vira combobox pesquisável (labels + códigos dos 13
  perfis, ignora acentos, navegação por teclado; sem JS o select canônico
  funciona); detalhe do caso (médico, agendador, NIR), página de
  trabalho/decisão e confirmação/negação do agendador no layout do
  ats-web; painel gerencial no visual (seletor de período, cards de
  métrica).
- **intake-batch-semantics** — semântica correta do lote, corrigida antes
  da fase 2 (antes, N PDFs produziriam UM caso com N pacientes):
  **cada PDF = um caso independente**, tipo único de procedimento por
  envio, anexos somente quando o envio tem exatamente 1 PDF, falhas
  parciais (PDF inválido rejeita só ele, casos dos válidos criados com os
  erros listados) e limites de lote descritos na UI. Novo serviço
  `submit_report_batch` no molde ats-web.

## [0.1.15] — 2026-09-22

**Referência humana (nº de ocorrência) em toda a UI** — 1 change
(`case-detail-human-reference`), 5 slices TDD com revisão independente por
slice. Suíte 1516 → **1553** (+37 testes). Sem migrations (deploy direto).

- O uid interno deixa de ser texto visível: `<title>`, breadcrumbs, hero,
  mensagens flash, alerta de lote, página «Reenviar corrigido» e badge de
  prior-case passam a citar o **nº de ocorrência** com fallback `uid <8
  chars>` (padrão do painel). URLs/rotas seguem com o uid como chave técnica.
- Detalhe do caso do NIR ganha card «Identificação do paciente» (nome +
  idade, badge de status no header — padrão scheduler/doctor) no lugar do
  hero «Caso <uuid>».
- Desambiguação onde há reenvio corrigido (mesmo nº): referência
  `nº · <8 chars>` no link de correção, página de reenvio, flash do novo
  caso e cards de «meus casos» com nº duplicado na aba corrente.
- `Case` expõe `short_id`/`display_reference`/`disambiguated_reference`
  (properties puras — fonte única do fallback, pinadas sem query);
  `agency_record_number` entra nos `.only()` das views com flash (sem query
  deferred).
- Specs `intake-nir`/`doctor-decision`/`scheduling` MODIFIED pinnam a
  ausência de uid completo em texto visível (evita reintrodução).

## [0.1.14] — 2026-09-16

**Contraste dos radio buttons** (quick fix, espelho do ATS v0.8.2).
Suíte 1511 → **1516**. Sem migrations (deploy direto).

- Radios não selecionados quase desapareciam sobre superfícies brancas
  (tipo de procedimento no upload). Borda de repouso 2px com o novo
  token `--hmd-control-border` (#78909c — 3.35:1 de contraste não-texto
  sobre branco, WCAG 1.4.11); `:checked`/`:focus` passam a usar os
  tokens do tema (primary/accent) em vez do azul default do Bootstrap.
  Guardas estruturais + cálculo de contraste em
  `apps/accounts/tests/test_theme_css.py`.

## [0.1.13] — 2026-09-16

**Reserva de caso (work lock) + página de trabalho única** — 1 change
(28º archive), espelhado do desenho validado do ATS Web. Suíte 1466 →
**1511**. Sem migrations (deploy direto).

- **`case-work-lock`** — a decisão médica acontece numa página única:
  a fila abre a leitura completa com o formulário no fim (âncora «Ir
  para o formulário de decisão», botões [Registrar decisão] e [Sair sem
  decidir] juntos). Ao abrir, o caso fica **reservado** para o médico
  (lease 1h renovável por heartbeat enquanto há atividade); outro
  médico vê «Reservado por {nome}» e é barrado; sair sem decidir
  libera na hora; a decisão grava só sob reserva vigente (posse
  validada sob row lock na mesma transação) e libera ao concluir;
  inatividade expira a reserva sozinha. Agendador espelhado (reserva ao
  abrir o detalhe agendável, confirmação/negação sob posse) — crítico
  para duas unidades trabalhando separadas. Filas mostram
  🔒 Reservado / ▶ Continuar / ação livre, com expiração lazy.

## [0.1.12] — 2026-09-15

**Procedimentos declarados × detectados visíveis nos detalhes** — 1 change
(27º archive). Suíte 1458 → **1466**. Sem migrations (deploy direto).

- **`detected-procedures-visible`** — o detalhe do NIR e o card
  «Procedimentos do caso» do detalhe médico exibem TODAS as rows de
  procedimento com origem (Declarado / Detectado na extração) e detecção
  pós-reconciliação (Detectado / Não detectado; sem reconciliação apenas a
  origem). Na retenção por divergência, o NIR decide «Liberar caso» com o
  resumo declarados × detectados ao lado — antes ia às cegas (caso real em
  dev: angio_art_perif declarada × art_perif detectada). Row detectada
  não-declarada sobrevive ao bypass e segue visível ao médico.

## [0.1.11] — 2026-09-15

**Correções de UX das filas** — 1 change (26º archive) + fix de template.
Suíte 1451 → **1458**. Sem migrations novas (deploy direto, sem `migrate`).

- **`queues-remove-uid`** — o uid interno (case_id) sai dos cards das três
  filas (Meus casos do NIR, fila médica, fila do agendador): nº de ocorrência
  + identificação do paciente bastam; **sem fallback** para uid quando o nº
  de ocorrência está ausente («—»), decisão do dono. O uid segue como chave
  técnica nas rotas (href).
- **Fix fila médica** (`</a>` ausente desde v0.1.4) — o card abria
  `<a class="list-group-item…">` e fechava com `</div>`: HTML malformado em
  cascata (cards sem fundo/limites, conteúdo vazando) visível com múltiplos
  casos simultâneos; teste de regressão pinnando o fechamento de cada card.

## [0.1.10] — 2026-09-15

**Dia do backlog pós-piloto (fase 2)** — 4 changes (22º–25º archives), política
 de PHI corrigida pelo dono: **zero-PHI é exigência do perímetro EXTERNO
 (LLMs/serviços de terceiros)**; a UI interna (funcionários do hospital)
 mostra a identificação completa. Suíte 1269 → **1451**.

- **`sesab-header-extraction`** — o cabeçalho padrão SESAB (repetido por
  página) passa a ser fonte determinística: **nome do paciente** extraído
  da linha de demografia (layout desalinhado da extração linear — o nome
  ia ao LLM em claro; agora `<PESSOA_1>` em todas as ocorrências, zero
  restantes), nome social como candidato PESSOA próprio, e metadados
  `patient_age`/`patient_gender`/`patient_race`/`days_on_screen`
  (Dias em tela = maior ocorrência, molde ats-web). Benchmark com corpus
  real como aceite; E2E no PDF do dono.
- **`doctor-detail-context`** — card de identificação do detalhe médico com
  Idade/Sexo/Raça-Cor e **ordem de leitura clínica**: Procedimentos →
  Sumário clínico → Estrutura extraída ANTES dos Alertas consultivos.
- **`queue-cards-wait-time`** — filas de médico/agendador **ordenadas por
  tempo de tela** (`days_on_screen` desc nulls-last, desempate FIFO) com
  cards nome+idade+⏱ Aguardando há/Recebido há+badge `N d em tela`;
  Meus casos do NIR com nome+idade (escopo por criador intacto).
- **`painel-ats-parity`** — painel em paridade com o dashboard ats-web:
  cards com nome/idade/**unidade de origem** (extraída do cabeçalho,
  campo novo)/exames/fase/data-hora+[Detalhes]; filtros data range + tipo
  de exame + busca por nome (AND); default **hoje/todos os estados**;
  **detalhe do caso no painel** com **trilha legível** (EVENT_LABELS
  100% do enum, anti-drift) collapsible + encerramento administrativo;
  trilha REMOVIDA de NIR/médico com badge «Falha no processamento".
- **`painel-lista-encerramento`** (21º, v0.1.9+) — lista de casos no painel
  + encerramento administrativo ≠CLEANED→CLEANED com minimização
  rows+arquivos, gates anti-worker-zumbi (defesa em 3 camadas), notificação
  ao criador (4º marco) e métrica de encerramentos.
- Fixes da fila médica (link «Abrir caso» nos cards — rotas inalcançáveis) e
  isolamento do `.env` do repo na suíte.

**Migrations**: `accounts.0005` (tipo de notificação ADMINISTRATIVELY_CLOSED),
 `cases.0010` (metadados do cabeçalho), `cases.0011` (`origin_unit`) — **o
 update requer `migrate`**.

## [0.1.9] — 2026-09-14

**Fix do incidente da fase 2 (mapa envenenado pelo NER)** (change
`anonymization-deterministic-first`, decisão do dono): o caso real
3ca56d86 falhara em `llm1_token_leak` de forma sistêmica — o NER do spaCy
classificava vocabulário clínico como PESSOA/LOCAL ("Afebril", "DORSO",
"PAS:100\nPAD:80", "FC:77\nFR:18", "Mot", "Prof", "Reg", "Macro"…) e esses
termos colidem como substring com qualquer artefato de LLM. Agora a fase 2
opera **determinística-first**: `ANONYMIZATION_USE_NER` default **False**
(engine Presidio e modelo spaCy nem são construídos — worker mais leve);
o mapa carrega apenas os identificadores do paciente extraídos dos rótulos
(nome/CNS/CPF/nascimento/nº de ocorrência, em TODAS as ocorrências do
texto), e o guard `llm1_token_leak` fica **confiável** (o incidente está
pinnado E2E e teria passado). **Identidade do paciente tokenizada ponta a
ponta agora também em motivos de negatura e anexos sem rótulo**: o
`seed_map` do caso faz varredura determinística dos valores conhecidos
(boundary-aware, sem inferência). Terceiros citados no texto seguem em
claro — postura da referência ats-web, decisão de política registrada e
reversível (`ANONYMIZATION_USE_NER=true` + benchmark para calibrar; env
passada nos 3 workers que anonimizam). Relatório de anonimização truthful
(`ner_enabled`; omite modelo/versões quando o NER não rodou). Nenhum PHI
vazou no incidente. Baseline 1188 testes.

Também inclui: diagnóstico privacy-safe no guard (loga contagem/tipos/
comprimentos, nunca valores — d6845cc) e passthrough OpenRouter no compose
dev (fase 2 reproduzível localmente).

## [0.1.8] — 2026-09-14

**Workers prontos para a fase 2** (change `phase2-workers-secrets`): a chave
da OpenRouter passa a ser lida **por arquivo** (`OPENROUTER_API_KEY_FILE`,
com precedência sobre a env e fail-closed em arquivo ilegível/vazio —
primitiva extraída de `db.py` com re-export, consumidores intactos) e é
montada **somente** nos workers que chamam a OpenRouter (`worker-llm`:
LLM1+LLM2; `worker-attachments`: LLM1+VISION) — `web`, `worker-pdf`,
`worker-anonymization` e `migrate` ficam sem nenhuma env OpenRouter (guard
anti-vazamento por mutação). `worker-anonymization` ganha
`ANONYMIZATION_SPACY_MODEL` ajustável (lg→md em host enxuto). Limites de
memória ajustáveis por env nos 4 workers (pdf 512m · anonymization **2560m**
— cluster 2×engine spaCy lg — · llm 512m · attachments 512m). Checklist de
ativação da fase 2 no README (pin da imagem nova ANTES dos workers — o helper
viaja na imagem —, `llm_check` dentro do worker, rollback por env).
`INTAKE_ENABLED` permanece `false` por default: nada ativa sozinho.
Baseline 1175 testes.

## [0.1.7] — 2026-09-14

**Semântica clínica do intake corrigida** (change `intake-batch-semantics`,
pré-requisito da fase 2 — alinhado ao ats-web por decisão do dono): cada PDF
do envio é o relatório de UM paciente e vira um caso independente em `NEW`
com 1 documento (antes: N PDFs mergeados num único caso de N pacientes).
**Um único tipo de procedimento por envio** (radio obrigatório; múltiplos
tipos abolidos). **Anexos somente com exatamente 1 PDF no envio** — lote com
>1 PDF cria os casos SEM anexos e reporta erro informativo; 1 PDF + anexo
inválido aborta tudo. Falhas parciais por arquivo (nome + motivo), incl.
falha de persistência (transação por caso). Reenvio do gate e reenvio
corrigido passam a exigir **exatamente 1 PDF** (UI coerente: sem `multiple`,
label singular, hint com o limite de MB). Limites operacionais configuráveis:
30 arquivos/envio, 20 MB/arquivo, 100 MB/lote (envs `INTAKE_MAX_*`,
recomendação Cloudflare). Contrato redirect×resultado: 1 caso+0 erros →
detalhe; lote/erros → página de resultado com contagem e erros por arquivo.

**Rótulos de papel em português na interface** (change `role-labels-ptbr`,
feedback do piloto): as chaves internas (`doctor`, `scheduler`, `manager`,
`nir`, `admin`) permanecem nos dados/sessão/permissionamento, mas a UI exibe
**médico/agendador/supervisor** nas 9 superfícies (badge de papel ativo,
seleção de papel — submetendo a chave crua —, home, perfil, trilhas de
eventos e comunicações dos detalhes de caso). Chaves fora do mapeamento
(`system`) exibem a própria chave (fallback seguro). Baseline 1160 testes.

## [0.1.6] — 2026-09-13

**Painel gerencial e home por papel** (change `painel-gerencial-e-home`): o
painel passa a ser exclusivo dos papéis `manager`/`admin` — link da navbar
visível apenas para esses papéis E rota protegida por `role_required` (403
para nir/doctor/scheduler; anônimo ao login). A home pós-login deixa de ser
um placeholder universal e direciona cada papel à sua área de trabalho:
`nir` → **Enviar relatório** (o caso de uso mais comum), `doctor` → fila
médica, `scheduler` → fila de agendamento, `manager`/`admin` → painel (com
fallback para sessões sem papel ativo válido). Menu do NIR reordenado:
`[Enviar relatório] [Meus casos]`. Notificações seguem coerentes
(manager/admin aterrisam no painel via home). Baseline 1118 testes.

## [0.1.5] — 2026-09-13

**UX de segurança do guard de intranet** (change `intranet-blocked-logout`,
de uso real do piloto): usuário exclusivamente restrito (`nir` puro)
bloqueado de fora da intranet tinha a sessão mantida e ficava preso — a
página de bloqueio era texto puro (sem navbar/logout) e `/login/`
redirecionava autenticados de volta à home bloqueada. Agora o guard **encerra
a sessão** no bloqueio e responde 403 com página própria e botão "Voltar ao
login" (funciona: anônimo → form). Cookie expirado na própria resposta
(max-age 0, assertado); log de auditoria `session_terminated=1`; multi-role
com papel externo segue acessando normalmente (regra de conjunto).
Housekeeping: README atualizado do "v0.1.0" para o estado real do piloto;
comentário stale do workflow. Baseline 1101 testes.

## [0.1.4] — 2026-09-13

**Hardening do runtime do piloto + carregamento dos changes pós-piloto**: a
imagem de produção passa a executar como usuário dedicado **não-root**
(uid/gid 10001) e o entrypoint WSGI passa a assumir settings de **produção**
por default (fail-safe). Entram também os dois changes fechados depois do
deploy do piloto: guard de intranet decidido pelo **conjunto** de papéis e
rótulos das unidades configuráveis por ambiente.

### Runtime de produção (hardening)

- **Imagem não-root** (`image-hardening-v0-1-4`, slice 002): o `Dockerfile`
  cria o grupo/usuário `hmd` (uid/gid **10001**, sem shell) e declara
  `USER 10001:10001` depois do `collectstatic` do build. `/app/media` nasce na
  imagem com o ownership do usuário, de modo que o volume nomeado (web e
  workers) o herde na primeira montagem. Nada em runtime exige root (gunicorn
  em 8000, secrets montados legíveis pelo uid 10001 (modo 0644; 0600
  root-owned aborta), `/tmp` em tmpfs 1777, estado no banco) e o comentário de
  dívida "non-root fica para o próximo release" **saiu** do compose —
  `read_only`, `tmpfs`, `no-new-privileges` e `cap_drop: ALL` seguem como
  defesa em profundidade.
- **WSGI fail-safe** (`image-hardening-v0-1-4`, slice 001): `config/wsgi.py`
  sem `DJANGO_SETTINGS_MODULE` assume `config.settings.prod` (a env explícita
  continua vencendo; `manage.py` mantém o default de dev para a CLI).
- **Desenvolvimento**: com o `USER` da imagem o fluxo dev quebraria (venv
  root-owned criado pelo `uv sync`); o `docker-compose.dev.yml` passa a
  declarar **deliberadamente** `user: "0:0"` nos 5 serviços de imagem
  (dev/teste como root, produção non-root), travado por teste estático.

### Changes pós-piloto carregados

- **Guard de intranet por CONJUNTO de papéis** (`intranet-any-role-egress`):
  o bloqueio externo vale apenas quando **todos** os papéis do usuário estão
  em `INTRANET_RESTRICTED_ROLES` — `nir` puro continua 403 fora da intranet,
  e quem tem algum papel externo (`{nir, manager}` com papel ativo nir, por
  exemplo) passa sem trocar de papel. ADR-0002 revisado.
- **Rótulos das unidades configuráveis** (`unit-labels-env`): envs
  `HMD_UNIT_1_LABEL`/`HMD_UNIT_2_LABEL` (defaults `Unidade 1`/`Unidade 2`) com
  fonte única (`apps/cases/units.py`) em toda a superfície — form, dashboard,
  fila/detalhes do agendador, detalhes do NIR, resposta final ao NIR (texto da
  unidade 2 **interpolado** com o rótulo configurado) e manual. O histórico de
  comunicações preserva o texto canônico da época da postagem.

### Deploy (piloto fase 1)

- **Pins/default na v0.1.4**: `${HMD_IMAGE_TAG:-v0.1.4}` no
  `docker-compose.prod.yml` e exemplos `v0.1.4@sha256:<digest>` no `README.md`
  e no `.env.example` (o digest é preenchido na publicação do owner).
- **Upgrade de volume pré-existente**: o volume `media_data` criado pela
  v0.1.3 é **root-owned** e NÃO herda o ownership do mountpoint da imagem nova
  (só volumes novos herdam). A fase 1 não escreve mídia (intake desligado),
  mas **antes de ligar os uploads (fase 2)** rode o re-chown pontual
  documentado no README: `docker compose -f docker-compose.prod.yml run --rm
  --user root --cap-add CHOWN web chown -R 10001:10001 /app/media`
  (`--cap-add CHOWN` é necessário porque o serviço derruba todas as
  capabilities).

**Baseline**: 1099 testes · ruff/format/mypy limpos.

## [0.1.3] — 2026-09-12

**Correção de falha real do migrate do piloto**: o backend de cache de
produção apontava para `django.core.cache.backends.database.DatabaseCache`
(path de import inválido — o módulo é `...backends.db`). Settings não
importam a string no load, então os asserts antigos passavam e o erro só
aparecia no `createcachetable`/runtime (`InvalidCacheBackendError`, rc=1
antes de tabelas/seeds; rollback local, app greenfield — sem dado perdido).
Regressões novas: `import_string` do backend de prod; `manage.py check`
com settings de prod (subprocess, envs dummy + secret em arquivo); roundtrip
`createcachetable` + set/get com `db.DatabaseCache` no banco de teste.
Baseline 1079 testes.

## [0.1.2] — 2026-09-11

**Adendos pós-release (compose/docs — sem nova imagem; a imagem v0.1.2 permanece
a referência de deploy)**: (a) topologia Cloudflared→Caddy→HMD — default
`TRUSTED_PROXY_HEADER=HTTP_CF_CONNECTING_IP` (Caddy repassa sem tocar; sem
`header_up X-Forwarded-For` nesse hop); (b) limites PLANEJADOS/INICIAIS de
fase 1 do web (512m / 1.0 cpu / 200 pids — revisar por observação; workers
exigirão faixas próprias) + hardening de container (rootfs read-only,
tmpfs /tmp 64m, no-new-privileges, cap_drop ALL) mitigando o risco residual
de root aceito na fase 1 (non-root fica p/ v0.1.3); (c) **correção P1**:
`DJANGO_SETTINGS_MODULE=config.settings.prod` no web (sem ela, o wsgi.py
defaulta para settings de DEV — DEBUG=True — pois o gunicorn não aceita
`--settings`); (d) verificação: extensões `unaccent`/`pg_trgm` do init.sql
de dev/teste não são usadas por código/migrations (normalização do
prior-case é NFKD puro em Python) — produção não precisa delas nem de
grants de EXECUTE.

Correções obrigatórias do blueprint (owner) para o piloto no HGRS: alias
estável do web, **todos** os segredos por arquivos read-only e `latest` fora
dos releases futuros.

### Deploy (piloto fase 1)

- **Alias estável `hmd`**: o serviço `web` declara
  `networks.hospital_ingress_hmd.aliases: ["hmd"]` — o upstream do Caddy é
  **sempre `hmd:8000`**, independente do nome gerado pelo compose (o README
  deixa de citar `hmd-prod-web-1:8000`).
- **Segredos por ARQUIVO** (`docker-compose.prod.yml`): top-level `secrets`
  (`app_db_password`, `migrator_db_password`, `secret_key`,
  `superuser_password`; caminhos configuráveis por `*_FILE`, default
  `./secrets/`) montados read-only, **um consumidor por segredo**: web/workers
  usam `secret_key` + `app_db_password`; o passo migrate usa `secret_key` +
  `migrator_db_password` + `superuser_password`. `DATABASE_URL`/
  `MIGRATOR_DATABASE_URL` saem do compose inteiro — o banco é montado por
  `DB_HOST`/`DB_PORT`/`DB_NAME`/`DB_USER` (+ `DB_PASSWORD_FILE`; o migrate usa
  `MIGRATOR_DB_USER`).
- **Runbook**: imagem pinada por **tag+digest**
  (`HMD_IMAGE_TAG=v0.1.2@sha256:<digest>`); `.gitignore` passa a ignorar
  `secrets/`.

### Configuração

- **`SECRET_KEY` por arquivo**: `config.settings.prod` lê
  `DJANGO_SECRET_KEY_FILE` (arquivo com **precedência** sobre
  `DJANGO_SECRET_KEY`; ilegível/vazio aborta com `ImproperlyConfigured`).
- **`DATABASES` de produção** resolvidas por `database_config` (`DB_*` +
  `DB_PASSWORD_FILE`), sem depender de `DATABASE_URL` no ambiente.
- **`seed_admin`** aceita `DJANGO_SUPERUSER_PASSWORD_FILE` (mesma semântica de
  precedência/fail-closed; o username segue em env não-sensível).

### Release

- **Sem `latest`**: o workflow publica apenas a tag da versão nos releases
  futuros — a imagem do piloto é pinada por tag+digest.

**Baseline**: 1074 testes · ruff/format/mypy limpos.

## [0.1.1] — 2026-09-11

Primeiro release **de produção** do HMD para o piloto no HGRS: runtime web em
imagem versionada, healthchecks, settings endurecidas e o compose produtivo.
O upload do NIR (intake) fica **desligado** nesta fase.

### Runtime de produção

- **gunicorn** (`>=23,<24`) como servidor WSGI do `CMD` da imagem (3 workers ×
  2 threads, timeout 60s / graceful 30s, access e error log em stdout/stderr —
  a rotação é do log driver do compose).
- **`collectstatic` no build** da imagem (estáticos + manifest do WhiteNoise
  dentro dela, com envs dummy de build); nenhuma etapa de estáticos no startup.
- **`/healthz`** (liveness, sem tocar o banco) e **`/readyz`** (readiness:
  `SELECT 1` + validação do cache `hmd_cache`) — públicas, isentas do guard de
  intranet e usadas pelo healthcheck do compose.

### Configuração e hardening

- **Cache `DatabaseCache`** em `hmd_cache` (tabela criada pelo passo migrate
  one-shot via `createcachetable`); guard anti-LocMem mantido em produção.
- **Settings env-driven**: `CSRF_TRUSTED_ORIGINS` (default
  `https://hmd.projetoshgrs.com`), `SECURE_PROXY_SSL_HEADER` (via
  `PROXY_SSL_HEADER`, Caddy termina o TLS) e `ALLOWED_HOSTS`.
- **`INTAKE_ENABLED` fail-closed**: criação de casos bloqueada no boundary de
  serviço (erro nomeado antes de qualquer validação/escrita/arquivo/enqueue) e
  nos POSTs do intake (flash + redirect); default **false** em produção e true
  em dev/teste.

### Deploy (piloto fase 1)

- **`docker-compose.prod.yml`** autônomo: web GHCR sem porta no host (Caddy
  como upstream na rede de ingress), PostgreSQL 17 compartilhado e externo
  (DB `app_hmd`), redes externas (`hospital-db-hmd`,
  `hospital_ingress_hmd`, `hospital_egress_hmd`), **migrate one-shot**
  (`migrate && createcachetable hmd_cache && seeds`, com credencial de
  migrator) e workers django-q2 sob profile **desligado**; secrets apenas por
  `${VAR}`, fora do Git.
- **Imagem `ghcr.io/carlosapgomes/hmd:v0.1.1`** publicada pelo workflow de
  release (linux/amd64, tag da versão + `latest`) no push da tag `v0.1.1`.

### Notas

- A ativação de **intake**, dos **workers** (pdf/anonymization/llm/attachments)
  e do **egress OpenRouter/OCR** (`OPENROUTER_API_KEY`/`VISION_MODEL`) será uma
  **outra mudança**; a fase 1 opera o ciclo assistencial com o upload bloqueado.

**Baseline**: 1061 testes · ruff/format/mypy limpos.

## [0.1.0] — 2026-09-10

Primeiro release do HMD para **testes internos** no HGRS: ciclo completo do
caso de ponta a ponta (`NEW→CLEANED`) com anonimização fail-closed, pipeline
LLM por tipo de exame, decisão médica consultiva, agendamento em 2 unidades,
anexos com OCR híbrido auditado e verificação de paciente, notificações
in-app, painel gerencial zero-PHI, PWA instalável (ícone HMD) e manual de
usuário por papel.

**Baseline**: 1037 testes · ruff/format/mypy limpos · 12 archives · 13 specs
promovidas.

### Changes (ordem de execução)

- **bootstrap-django-hmd-core** — scaffold Django 5.2 + uv, settings por
  ambiente (base/dev/test/prod), toolchain de qualidade, compose de
  desenvolvimento e de teste, contas com multi-role e papel ativo, guard de
  intranet.
- **ad-kerberos-authentication** — login AD por CPF+senha no form (AS-REQ
  server-side via minikerberos pinado, provisionamento por UPN no admin,
  failover entre DCs por transporte; sem SPNEGO/keytab), lockout/rate-limit
  anti-brute-force; admin é identidade local por design (emenda
  `admin-local-identity`: gate e flag extintos, ADR-0009).
- **case-core-fsm-procedures** — FSM de 17 estados com transições protegidas
  (django-fsm-2), catálogo de 13 tipos de procedimento, procedimentos por
  caso atômicos, locks de concorrência, trilha de eventos append-only e
  thread de comunicações (user/system).
- **intake-nir-upload** — upload do relatório (1..N PDFs) pelo NIR com
  gate de qualidade (formato/OCR/padrão), worker de extração (cluster pdf),
  "Meus casos" com detalhe por criador.
- **presidio-anonymization** — anonimização determinística + Presidio
  (pt-BR, spaCy lg), pseudônimos `<CATEGORIA_N>` com linkage do caso,
  re-identificação na renderização, worker próprio, benchmark com corpus
  sintético versionado.
- **llm-pipeline-per-type** — cliente OpenRouter, prompts versionados
  (seeds idempotentes), LLM1 de extração e LLM2 de sumário/policy por tipo
  de exame (schemas strict), sugestão consultiva com motivos, prior-case
  (7d nº registro / 15d nome+nascimento), orchestrator fail-closed no
  cluster llm; **LLM só vê texto anonimizado** (asserts recursivos).
- **doctor-queue-decision** — fila médica por especialidade (generalista
  vê tudo), presenter re-identificado na renderização (identificação,
  história, timeline, alertas, prior-case, requisitos gerais), decisão por
  procedimento com motivo obrigatório na negativa.
- **scheduler-multi-unit** — fila do agendador (abas aguardando/decididos),
  confirmação/negação por unidade (unidade 1 com local/data parametrizados;
  unidade 2 texto fixo), PDF do relatório por posição (gate
  `scheduled_by==user` + status pós-decisão; 404 fail-closed), resposta
  final ao NIR por unidade, reabertura por intercorrência (só unidade 1).
- **nir-result-closure** — resposta final de negativa médica (motivos
  reais por procedimento), resultado e casos encerrados ao criador,
  ciência do NIR com limpeza transacional e minimização (documentos e
  artefatos clínicos removidos; identificação, decisões, trilha,
  comunicações e agendamento preservados; prior-case continua elegível),
  reenvio corrigido como novo caso vinculado com motivo obrigatório.
- **attachment-processing-ocr** — anexos jpg/png/pdf na criação e no
  reenvio (limites de contagem/tamanho/MIME), OCR híbrido (PDF com texto
  local via PyMuPDF; foto/scan/PDF-imagem via `VISION_MODEL` externo com
  evento de auditoria antes do envio — anuência formal registrada),
  anonimização no namespace de tokens do caso (semeadura) e verificação
  LLM de paciente **só com tokens** (`match|mismatch|unknown`;
  mismatch = alerta consultivo, nunca descarta), cards re-identificados na
  decisão médica, ciência remove anexos (rows+arquivos).
- **dashboard-notifications-pwa** — notificações in-app por marcos
  (resposta final e reabertura → criador; pronto para agendamento → todos
  os agendadores ativos) com sino/badge, lista com janela de 48h e redirect
  por papel ativo; painel gerencial por período (hoje/7d/30d/tudo) com
  quebra por tipo/unidade e tempo médio até decisão (fontes imutáveis,
  zero-PHI, acesso transversal); PWA instalável (manifest HMD, ícones
  HMD gerados por script, service worker conservador com bypass de rotas
  de PDF); manual de usuário por papel com os 17 estados reais.

### Pendências conhecidas (pré-produção)

Benchmark de anonimização com corpus real · `VISION_MODEL` no ambiente para
OCR externo de anexos · revisão humana dos 29 prompts · validação do
qcluster/workers em ambiente real · serviço web de produção com gunicorn
(hoje o Dockerfile é dev/runserver) · decisão de produto: negativa total na
aba "decididos" do doctor · texto informativo do parâmetro K.
Detalhes em `PROJECT_CONTEXT.md`.
