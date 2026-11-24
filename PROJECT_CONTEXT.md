# PROJECT_CONTEXT.md

## Propósito

Resumo executivo para retomada rápida após pausas e onboarding de
implementadores com contexto zero.

## Fontes autoritativas

- `AGENTS.md` — regras, stack, comandos, política de testes, workflow.
- `README.md` — instruções de ambiente e operação local.
- `openspec/changes/` — changes ativos (proposals, designs, specs, slices).
- `docs/adr/` — decisões arquiteturais (ADR-0001 a 0003 no bootstrap).
- Roadmap e parâmetros de negócio: `temp/plano-implementacao-hmd.md` e
  `temp/parametrosHMD.md` (fonte para os tipos de procedimento — 13 originais;
  neurointervenção com critérios S2 adicionada em 2026-11-22 por decisão do
  dono — e thresholds
  clínicos — consumidos a partir do change 03).
- Projeto-fonte de padrões (somente-leitura): `/projects/dev/ats-web`.
- Em caso de conflito, artefatos mais recentes no Git prevalecem; decisões
  registradas em ADR prevalecem sobre texto de slice.

## Objetivo do sistema

Sistema de **apoio à regulação de pacientes do serviço de hemodinâmica** do
HGRS. O NIR envia relatórios de regulação (PDF) com a declaração dos
procedimentos; o sistema extrai, anonimiza (Presidio, obrigatório antes de
qualquer LLM), estrutura via LLM e apresenta ao médico uma recomendação
**consultiva** (nunca bloqueia); após a decisão médica, o agendador confirma
data/hora/local (unidade 1 ou 2) e o resultado retorna ao NIR.

Diferenças fundamentais vs. o projeto-fonte `ats-web` (EDA): autenticação via
Active Directory (Kerberos) em estágio posterior; anonimização Presidio
fail-closed; catálogo de 14 tipos de procedimento com schemas/prompts por tipo;
policy clínica consultiva determinística; agendamento multi-unidade;
django-fsm substituído por django-fsm-2 (MIT; API django-fsm preservada — viewflow.fsm descartado por AGPLv3+) com estados renomeados.

## Estado atual (execução do roadmap)

**Change `bloqueio-troca-de-senha` (2 slices + gate, ARQUIVADA em 2026-11-24)**:
dono removeu do aplicativo toda affordance de troca de senha — a senha é do AD e
só é trocada presencialmente na TI do hospital (ADR-0011). S1 `9625e32`: perfil
só de leitura (`@login_required` por fora + `@require_GET` por dentro → 405 em
POST, sem leitura de `request.POST`), card «Alterar senha» removido, dados em
largura total (`col-12`), linha exata «Precisa trocar a senha? Procure
pessoalmente a TI do hospital.», `HospitalPasswordChangeForm` extinta (nunca
funcionara para AD: `check_password` com senha inutilizável), harness
`sessoes` com checagens negativas e de VISIBILIDADE da orientação. S2
`0f92a4a`: as TRÊS rotas herdadas do Django admin sem o projeto as ter pedido
(`admin:password_change`, `admin:password_change_done` e
`admin:auth_user_password_change`) saem por filtragem POR NOME em
`HmdAdminSite.get_urls`/`UserAdmin.get_urls` (autoatendimento → 404 pelo
catch-all); override de `templates/admin/base.html` sem o link «Alterar senha»
(o `{% url %}` derrubaria TODAS as páginas do admin com `NoReverseMatch`) com
teste anti-drift contra o original do Django instalado (upgrade que toque o
`base.html` quebra o teste por desenho); formulário de edição de usuário sem
botão de senha e com help_text neutro REAFIRMADO após `super().__init__` (o
`UserChangeForm` da base o sobrescreve para senha inutilizável); template do
widget no NÍVEL DO APP (`apps/accounts/templates/accounts/widgets/` — primeiro
diretório de templates por app do repo; o renderer de formulários só busca em
`django/forms/templates` + `templates/` dos apps, nunca no `DIRS` do projeto);
varredura recursiva do resolver com prefixos acumulados (zero padrões com
`password`). Decisões do dono: template no app (1-a); rota por usuário com
comportamento nativo — 302 (redirect de compatibilidade do `ModelAdmin`) → 302
(objeto inexistente → índice do admin com aviso) → 200, sem página de senha e
sem escrita; nem o padrão de compatibilidade nem o redirect de objeto
inexistente são filtrados/sobrescritos; campos de senha do formulário de
CRIAÇÃO de usuário (`add_fieldsets`) FORA de escopo. Operação: o admin local
perde o autoatendimento — senha rotacionada pela TI com `manage.py
changepassword <username>` (README, seção «Autenticação AD»; `seed_admin` em
re-execução segue sem resetar). Execução: 2 loops `/slice-loop` (worker
meta/muse-ember-1.0:medium + reviewer openai-codex/gpt-6.1-terra:high; 2
rodadas e 1 ciclo de fix por slice; S2 com 2 tentativas bloqueadas por decisão
humana — erros do DESENHO do controlador: caminho do template e a falsa
premissa de que a página de edição daria 404; evidências em
`/tmp/hmd-slice-runs/hmd/remove-pw-change/bloqueio-troca-de-senha/`). Suíte
2842→2861. Specs: `account-access` +2 requisitos ADDED (8 cenários);
`validate --specs` 18/18 (avisos --strict de texto longo são os 6 pré-existentes).
Notas de processo: o gate do host (`acceptance.verify`) só executa no nível
`verified` — em `checked` o `verifyRuns` vem vazio; CHANGELOG fica para o
release. Commits 9625e32 + 0f92a4a.

**Change `registro-profissional-resposta-final` (2 slices + gate, ARQUIVADA em 2026-11-23)**: exigência SUREM — nome e CRM de quem autorizou/aceitou/recusou o exame no retorno ao NIR. Descoberta: o par `professional_council`/`professional_council_number` (+ property `professional_registration_display`, consumida no perfil) já existia desde o bootstrap mas estava inerte — SEM superfície de edição no Django admin. (001) fieldset «Profissional» no `UserAdmin` (fieldsets+add_fieldsets, molde ats-web; validação par-ou-nada permanece só em `User.clean`; testes de estrutura+POST add/change); (002) helper único puro `apps/cases/doctor_credit.py::doctor_credit_line(case)` — lê `Case.doctor_decided_by` (nunca o `user` do post), `.strip()` defensivo — compondo a última linha `Médico responsável: {nome} — {CRM N}` nas 3 respostas finais ao NIR (confirmação U1/U2 em `confirm_case_scheduling` — `reply_unit_2_text()` preservada sem argumentos, composição no serviço — e negativa médica em `post_doctor_denial_reply`), degradação graciosa (sem registro → só nome; decisor nulo → omite; nunca falha), `|linebreaksbr` na thread nas 3 superfícies (NIR detalhe/histórico + agendador; corrige também o colapso visual dos itens da negativa), manual cita a linha. Fora de escopo (R6): negação de agendamento e intercorrência sem a linha (pins); previews de notificação intactos. Thread append-only = snapshot no post (D4). Execução: 2 loops pi-subagents-openspec-loop/2 standalone (worker muse-ember-1.0:high + reviewer muse-wonder-1.0:medium; evidências /tmp/hmd-slice-registro-00{1,2}-run/; ambos aceitos na 1ª rodada de review — OK with notes; plano revisado por reviewer independente antes: M1/M2/m1–m4 quitados, incl. mapa de testes auditado: só `test_closure_denial.py` e `test_views.py:1478` quebram por igualdade estrita; `test_services.py:199/245` verdes pela degradação; `:381`/incident/unit_labels pins intocados). Suíte 2420→2431. Specs promovidas ×3 MODIFIED (account-access +3 cenários admin; scheduling linha do médico na resposta final — pin «exatamente» da U2 reconciliado; case-closure idem negativa); validate --specs 17/17 (warnings --strict de texto longo são pré-existentes em todo o corpus). Notas operacionais: pytest desta estação exige `TEST_DB_PORT=55438` (5433 hospeda outro serviço); `display_name` de usuário AD sem `first_name`/`last_name` cai no CPF — provisionamento médico DEVE preencher nome (D2). P2s de backlog: render `<br` positivo só no detalhe NIR (parametrizar p/ 3 superfícies), manual não menciona degradações, RED intermediário do helper. Commits 4bb86b9 + 10dce81.

**Change `reapresentacao-casos-filas` (5 slices, ARQUIVADA em 2026-11-24)**:
identidade conservadora por nome normalizado/procedimento efetivo + regulação,
CNS estrutural SESAB ou origem (fraca); CNS conflitante, idade forte divergente
ou múltiplos candidatos exigem conferência NIR. S1 `17fe1f2`: CNS/reset e
prior-case com janela nova de 15 dias. S2 `e191d0a`: substituição médica apenas
com novo pronto, espera herdada sem alterar criação/dias em tela, vínculo/FSM,
mutex versionado antes de locks ordenados, retry limitado/idempotente e comando
`reenqueue_pending_reappearances` (simulação por padrão, `--apply` só agenda).
S3 `4a84552`: reenvio de agendamento arquivado somente pós-LLM1/reconciliação,
preservando original/aprovação/reserva, documentos recebidos e disposição/purga
por grupo. S4 `0b056af`: contador/documentos recebidos/espera/histórico retido do
agendador, sem ampliar o gate do PDF original. S5 `9ecaf9e`: comparação read-only
no detalhe NIR da equipe com exatamente duas respostas por par/revisão (mesmo
pedido ou independente), silêncio sem bloqueio e contexto tardio honesto no
card médico, sem reanálise automática, triagem clínica ou suspensão da agenda.
Specs promovidas em 7 capacidades (nova `case-reappearance` + 6 existentes),
16 requisitos novos e 7 modificados, preservando cenários não alterados.
Validação final S5: ruff/format/mypy/Django check, **2818 testes distintos**
(suíte principal com 1 teste de settings isolado para evitar override de
`TEST_DB_NAME`), migrations sem drift e jornada oficial verify-hmd S5 **28/28**
PASS em desktop/mobile com cleanup confirmado. Review S5 2/2: OK, zero achados;
977 arquivos idênticos ao snapshot revisado antes/depois da validação.
Evidência final: `/tmp/hmd-s5-final-validation-20261124/acceptance.md` e
`tmp/verification/hmd-verify-9aaeb8192f5d40be/`. Archive:
`openspec/changes/archive/2026-11-24-reapresentacao-casos-filas/`.
Rollout de produção NÃO executado: `CASE_REAPPEARANCE_ENABLED` continua default
false em prod; ativação exige smoke controlado de workers reais. Rollback
preserva vínculos/documentos/métricas já aplicados, sem ressuscitar originais;
lookup legado só com flag off. Notas P2 anteriores continuam deferidas: S1
backstop de fixture/manual legado; S2 AUD-12/helper duplicado, AUD-13/driver
superseded e P2-3/P2-4; S3 docstring FSM e nota RED 3C; S4 release do viewer
mobile conforme padrão doctor, prova R4 via update vs e2e e typo de fixture RED.
As specs principais passam validação normal; strict possui avisos de tamanho de
descrições (>500 caracteres), já presentes antes da promoção (deltas aprovados
preservados sem refatoração normativa fora de escopo).

**Change `visibilidade-por-equipe` (7 slices + gate, ARQUIVADA em 2026-11-22)**: escopo de EQUIPE nos três papéis, motivada por incidente real de produção (login break-glass ≠ criador → lista vazia interpretada como "casos sumidos"). (001) lista NIR renomeada «Casos em Processamento» com todos os ativos de qualquer criador (rótulos em nav/h2/título sentence-case/alerta pós-lote/breadcrumb, contagem da equipe, manual NIR); (002) detalhe ativo + serve/viewer de PDF e anexos a qualquer `nir` (guard de papel, paridade serve×viewer); (003) gate + confirmação de divergência por qualquer `nir` NA CAMADA DE SERVIÇO — `_assert_owned_by` extinto, `_assert_nir_role` (re-check de papel NA transação consultando vínculo no banco) + `PermissionError→403` nas views (nunca 500); novo evento `CASE_GATE_DOCUMENTS_RESUBMITTED` com operador como ator (P1 do review: uploaded_by não é trilha); (004) ciência por qualquer `nir` (closure com re-check transacional, ator=operador) + reenvios corrigidos sem partição `is_own` (ativo→detalhe, encerrado→histórico) e redirect de recusa unificado; (005) «Decididos Hoje» da ESPECIALIDADE (`doctor_decided_by__isnull=False` + subtipo do visualizador, decisor no card via select_related, badge `queue_counters` coerente, manual médico); (006) «Processados Hoje» da equipe de agendamento (idem, «Processado por …»); (007) PDF/texto do detalhe do agendador por DECISÃO DE AGENDAMENTO + estado (`_scheduler_can_view_pdf = scheduling_decided_at is not None and status in PDF_ALLOWED_STATUSES`, helper único nos 4 pontos incl. histórico; **guarda anti-negativa-médica** com teste negativo explícito — decisão do dono: equipe de agendamento já acessa prontuário eletrônico; alargamento só para casos COM decisão). Execução: loops pi-subagents-openspec-loop/2 por slice (worker muse-spark-1.3-contributor:high + reviewer gpt-6.1-terra:high; S1 e S3 exigiram fix cycle; 2 rounds máx respeitados; checkpoints em /tmp/hmd-slice-runs/...). Specs promovidas: 15 requisitos MODIFIED em 7 capacidades (intake-nir ×5, case-closure ×3, scheduling ×3, doctor-decision, pdf-original-viewer, attachments, ui-shell — rótulo «Casos em Processamento» como desvio deliberado do vocabulário ats-web). Suíte 2369→2401. Quirks: títulos de cenários antitéticos preservados por design (D8 — matching MODIFIED por nome); notificações seguem criador-targeted (Non-Goal/backlog do dono).

**Micro-change `p2-hardening-visibilidade-equipe` (2 slices, skip_specs, ARQUIVADA em 2026-11-22)**: quitou P2s deferidos — convergência do re-check de papel ao helper com mensagem por ação (paridade provada por testes intocados), remoção do parâmetro morto de `_decided_today_cases`, asserção de negação alheia no partial, testes dedicados do gate em `AWAITING_NIR_ACK` (via `nir_acknowledge()` FSM real) e manual do agendador preciso («à equipe de agendamento» + estados pós-decisão). Execução direta pelo controlador (autorização do dono), com provas de mutação pontual. Suíte 2401→2403.

**Change `transport-badge` (3 slices + gate, ARQUIVADA em 2026-11-22)**: badge de
transporte do relatório de regulação de ponta a ponta — descoberta no corpus
real (9/9 PDFs com o label `TRANSPORTE:`); (001) extração determinística no
worker de PDF (regex linha-âncorado com whitespace horizontal — valor na
mesma linha; sem delimitação de seção, D1; última ocorrência vence sobre o
texto do caso na ordem declarada, D2; domínio fechado `basic|mobile_icu` com
`BÁSICA`→basic e rejeição silenciosa de desconhecidos, D3) + `Case.transport_type`
(TextChoices, labels canônicos) + migration 0015 aditiva sem backfill (D5) +
ciclo de vida: reenvio zera na transação do resubmit, `CLEANED` preserva em
ambos os modos por ausência de `_CLEANED_EMPTY_VALUES` sem tocar closure.py
(D6), evento de extração sem o valor; (002) badge 🚑 nos cards das três filas
(NIR, médica nas 2 abas, agendador nas 2 abas) — «UTI Móvel» `text-bg-warning`
com ênfase, «Ambulância Básica» `text-bg-info` neutra, rótulo sempre do
`TextChoices` e classe do mapeamento único `TRANSPORT_BADGE_CLASSES` em
`apps/cases/models.py` (paridade de sinal entre papéis; ausente → sem badge);
(003) linha «Transporte» no card de identificação do detalhe médico (dt/dd
com `|default:"—"`, espelhada na página de decisão pela partial
`_case_cards.html` compartilhada). Pipeline LLM/anonização intocado. Execução:
loops pi-subagents-openspec-loop/2 por slice (worker muse-spark-1.3-contributor:high
+ reviewer gpt-6.1-terra:high; slice 001 exigiu 1 fix cycle — 2 P1 de lacuna
de teste; 002/003 limpos no round 1). Specs promovidas ×3 (intake-nir +2,
doctor-decision +2, scheduling +1 ADDED; validate --specs --strict 17/17).
Suíte 2331→2369. Quirks documentados nos slices: divergência nominal do
Read-first do slice 002 (`_case_cards` vs `_queue_content` — fila é
`_queue_content.html`; `_case_cards.html` é do detalhe/decisão).

**Change `paridade-ats-filas-historico-intercorrencia` (5 slices, ARQUIVADA em 2026-11-21)**: paridade operacional com o ats-web — (001) sumário clínico COMPLETO re-identificado ao agendador no detalhe (truncamento à 1ª linha removido; cards seguem com one-liner); (002) intercorrência pós-encerramento: FSM `CLEANED→AWAITING_SCHEDULING` + serviço `open_post_closure_issue` (atomic/lock, motivo LIVRE obrigatório — reuso do padrão do desmarcar, decisão do dono; evento `CASE_POST_CLOSURE_ISSUE_OPENED` com motivo+snapshot admin), marcador canônico `clinical_data_disposed_at` (gravado na minimização E na purga; backfill; emenda ADR-0010), issue única zerada em QUALQUER chegada a CLEANED (ciência ou encerramento administrativo), fan-out de notificação aos agendadores no marco de reabertura (spec notifications MODIFIED — vale também para a pré-ciência); (003) busca NIR de encerrados por nº/nome de QUALQUER criador (cap 50, referência humana, guarda de escopo histórico própria sem afrouxar guards por criador; detalhe read-only; POST inelegível → 400 re-render sem escrita) + nav «Buscar Encerrados»; (004) «Histórico» do agendador (universo por carimbos `scheduling_decided_at/by` — confirmação E negação carimbam no atomic, nunca zerados pela reabertura; migration 0013 com backfill por eventos fail-closed; badge honesto de reaberto; dados de agendamento anteriores no resultado) + detalhe em leitura com gate de PDF intacto; (005) abas «Decididos Hoje»/«Processados Hoje» por decisor com janela do dia local (helper único `apps/cases/day_bounds.py`; carimbos `doctor_decided_at/by` no atomic da decisão + migration 0014 backfill auditável + anti-drift; caso de ontem fora das abas; encerrado-hoje dentro; decisor nulo fail-closed; manual atualizado com a PERDA APROVADA do histórico médico — paridade ats-web). Política de PHI do dono registrada no design D1 (prontuário eletrônico como referência; fronteira LLM intocada). Execução: loops pi-subagents-openspec-loop/2 (worker muse-spark-1.3-contributor:high + reviewer gpt-6.1-terra:high, máx 2 rodadas/slice, gate independente da pai; 2 slices exigiram ciclo de fix; 1 P1 aberto contra verdict leniente → ciclo aberto pela controladora). Specs promovidas ×5 (scheduling MODIFIED×2+ADDED, doctor-decision MODIFIED, intake-nir ADDED, notifications MODIFIED) + NOVA `post-closure-issue`; validate --specs --strict 17/17. Suíte 2221→2314. **Micro-change `tests-hardening-paridade-ats` (skip_specs, ARQUIVADA)**: quitou P2s deferidos — histórico testado pelo caminho real (`open_post_closure_issue`), CLEANED+PDF mesmo decisor (cards ausentes+404), fluxo backfill 0014 até a aba do decisor. Pendências/residuais: P2 #1 (RED da corrida — documentado), smoke runtime das abas no piloto, premissa do backfill revisar em produção antes de flip/purga, verify-hmd skills citam rótulos antigos.

**Change `reenvio-corrigido-qualquer-nir` (2 slices, ARQUIVADA em 2026-11-21)**: decisões do dono — (001) reenvio corrigido aberto a QUALQUER usuário com papel ativo `nir` (guarda por papel em view E serviço — `_assert_owned_by` removido; validações 1 PDF/1 tipo/motivo intactas; paridade ats-web `get_object_or_404` sem filtro de criador; reenviador = criador do novo ciclo: notificações+ciência passam a ele); ação «Reenviar corrigido» no detalhe histórico; navegação sem 404 para não-criador (breadcrumb/cancelar/recusas → detalhe histórico; caso alheio fora do escopo → busca; recusas seguem redirect testado); links condicionais em «Reenvios corrigidos» (próprio→detalhe; alheio ativo→referência sem link; alheio CLEANED→histórico). (002) consolidação do nav NIR (opção A): my_cases SÓ ativos (`?tab=closed` legado → fallback seguro preservando filtros), nav 3 itens com «Casos Encerrados» = busca global (URL intacta), badge closed removido, manual atualizado; selo «Encerrado administrativamente»+motivo migrado ao detalhe histórico (órfão da aba removida — P1 do review, fix com extensão de escopo autorizada pela controladora). Plano revisado por reviewer independente (NEEDS-REWORK: BLOCKER guarda no serviço + 2 MAJORs navegação/links — resolvidos antes de codar). Loops pi-subagents-openspec-loop/2 (2 BLOCK→fix→OK). Specs promovidas: case-closure ×2 + intake-nir ×2 MODIFIED; validate --specs --strict 17/17. Suíte 2325→2331. **Residual preexistente (backlog)**: motivo do admin-closure não projetado em `event_detail_lines` após reabertura; `.pi/` skills citam `?tab=closed` (dir ignorado).

Changes 01–10 arquivados (11 archives; 10 specs promovidas). Change 11 `dashboard-notifications-pwa` arquivado (2026-09-10). Change 12 `pilot-deployment-v0-1-1` **executado e ARQUIVADO** (2026-09-13; 5 slices: runtime gunicorn/healthz, INTAKE_ENABLED fail-closed, compose prod, GHCR release workflow, alias `hmd`+secrets por arquivo; spec `production-deployment` promovida — 5 requisitos; releases v0.1.1/v0.1.2/v0.1.3, deploys pinados tag@digest; **piloto em produção autenticando via AD desde 2026-09-13**). Total: 17 archives + 14 specs. Change 18 `trilha-eventos-todos-perfis` arquivado (2026-11-21; 3 slices com loops worker/reviewer independentes): trilha de eventos disponível a TODOS os perfis no final do detalhe do caso (card collapsive fechado por padrão, partial único `templates/cases/_event_trail.html` + builder `apps/cases/event_trail.py` — labels/badges MIGRADOS do dashboard, anti-drift 45/45 em `apps/cases/tests/test_event_trail.py`; ordem cronológica ascendente, `select_related("actor")`, paridade por construção); painel inalterado em comportamento; decisão do dono B (2026-11-21): paridade total — texto livre da justificativa da revisão visível na trilha em todas as superfícies (re-escopo do pin «auditoria terse» do `test_procedure_review_recovery.py`: `sched.args`/payload/caplog/503 seguem PHI-free, HTML do detalhe não mais coberto); `decision_event` médico derivado da lista carregada; specs MODIFIED ×4 (intake-nir, doctor-decision, scheduling, account-access — cenários de autoria novos; quirk verbatim D5b preservado). Change 17 `painel-gerencial-e-home` arquivado (2026-09-13; dashboard MODIFIED acesso manager/admin; account-access ADDED home por papel ativo). Change 16 `intranet-blocked-logout` arquivado (2026-09-13; spec account-access MODIFIED: bloqueio encerra sessão + página com retorno ao login). Change 15 `image-hardening-v0-1-4` arquivado (2026-09-13; spec production-deployment MODIFIED ×2: runtime não-root uid 10001 + fail-closed inclui default WSGI prod; release v0.1.4 publicada no GHCR). Change 14 `unit-labels-env` arquivado (2026-09-13; spec scheduling: MODIFIED confirmação (texto da unidade 2 com label configurado) + ADDED rótulos configuráveis por ambiente; 4 superfícies de exibição cobertas por testes de override, incluindo belt-and-braces dos details e preservação do texto histórico da thread). Change 13 `intranet-any-role-egress` arquivado (2026-09-13; spec account-access: requisito do papel ativo REMOVIDO, substituído por "Acesso externo restrito a conjuntos exclusivamente restritos" com 4 cenários; ADR-0002 revisado). **Visibilidade de ponta a ponta (roadmap completo)**: `UserNotification` em apps/accounts (migration 0004) + signal `CaseEvent.post_save` com conjunto fechado de marcos (resposta final→criador com preview por payload.source; pronto-p/agendamento→fan-out schedurers ATIVOS distinct; reabertura=`AWAITING_SCHEDULING` com payload.reason→criador; idempotente por unique(recipient,event)+get_or_create; savepoint no signal — DatabaseError da notificação nunca reverte a transição em silêncio; zero PHI — motivo da reabertura nunca vaza); UI: sino+badge via context processor em toda página, lista com janela de retenção 48h (NOTIFICATION_READ_RETENTION_HOURS; nada apagado), abrir→marca lida+redirect por papel ativo (rotas fazem guard real; alheia=404), marcar-todas, endpoint JSON (rotas GLOBAIS sem namespace — accounts é incluída na raiz); app `apps/dashboard` com métricas puras por período (hoje/7d/30d/tudo; dia local) em **fontes imutáveis** (outcome=source do ÚLTIMO evento final SÓ com status pós-final — reaberto em voo=em andamento, sem dupla contagem; DISTINCT ON por -id; tabela por tipo=rows de CaseProcedure doctor_disposition na ordem canônica; unidade por scheduled_unit; tempo médio max(doctor_decided_at)−created_at) — view login-required transversal, template zero-PHI, link Painel; **PWA**: manifest HMD + sw.js conservador (bypass `includes("/pdf/")` E `"/documents/"` — viewer nativo; nunca POST; HTML autenticado nunca em cache) + ícones HMD SVG+10 PNGs por `scripts/generate_pwa_icons.py` (PyMuPDF, --check) + registro em base.html; manual por papel com os 17 estados reais (anti-drift bidirecional). **Anexos de evidência de ponta a ponta**: novo app `apps/attachments` (`CaseAttachment` com `original_filename`, status `pending|processing|processed|failed`, `extraction_method local_pdf|vision`, textos/mapas/verificação; migration 0001) — upload jpg/png/pdf pelo NIR na criação E reenvio corrigido (kwargs aditivos `attachments=()` em `create_case_with_documents`/`create_corrected_resubmission`; validação contagem≤5/tamanho≤10MB/MIME antes de qualquer gravação; arquivo por UUID no callable antes do INSERT + compensação); **extração híbrida** (PDF com texto → PyMuPDF local; foto/scan/PDF-imagem → `VISION_MODEL` externo com rasterização pixmap, teto 10 páginas, `ensure_vision_ready` fail-closed; **evento de auditoria `CASE_ATTACHMENT_EXTERNAL_OCR_DISPATCHED` antes do envio — só quando o envio é possível**); worker `process_case_attachments` no **cluster `attachments`** (`ATTACHMENTS_RUN_TASKS_INLINE`, default false em prod — fail-closed como os irmãos) com **idempotência por etapa** (retry não re-envia OCR) e no-op em corrida com a ciência; trigger signal `CASE_ANONYMIZATION_COMPLETED` → on_commit (anexos em paralelo ao pipeline LLM); **anonimização com semeadura do namespace do caso** (`anonymize_text(text, seed_map=...)`/`PseudonymOperator(seed_map)` aditivos: paciente do caso mantém seu token em qualquer ordem, paciente diferente ganha token distinto acima do máximo — sem convergência espontânea entre textos, a numeração é por 1ª ocorrência); **verificação LLM só vê tokens** (`ATTACHMENT_VERIFICATION`, 29º seed idempotente; `LLM1_MODEL` + schema strict `patient_match match|mismatch|unknown`/summary/evidence; sem token do paciente → unknown forçado em código; persistência atômica + `CASE_ATTACHMENT_PROCESSED`; falha → failed sem efeito no caso); **cards consultivos no doctor** (badge por patient_match/status, resumo/evidência re-identificados na renderização com o mapa do ANEXO via `reidentify` puro; mismatch = danger + alerta fixo, NUNCA descarta/bloqueia — decisão com mismatch testada OK); **ciência remove anexos** (rows no atomic D2 + arquivos no mesmo on_commit; eventos append-only; conjunto 09 intacto — delta MODIFIED case-closure com 6 cenários).

Notas operacionais: portas host 5432/5433/55433 flutuam entre projetos — use `POSTGRES_HOST_PORT=55432`/`TEST_DB_PORT=55435`. Produção exige NTP + cache compartilhado + workers no ar (flags `*_RUN_TASKS_INLINE=false`); LLM em prod exige `OPENROUTER_API_KEY` + `LLM_MODEL_*` (modelos por env). Diagnósticos: `manage.py ad_check` (AD), `manage.py llm_check` (OpenRouter), `manage.py anonymization_benchmark --corpus <real>` (**aceite pré-produção: relatórios reais e revisão de recall por entidade**), `manage.py seed_prompts` (idempotente). Pendências de validação em ambiente: qcluster real (worker-pdf/worker-anonymization/worker-llm/worker-attachments — precisa VISION_MODEL no ambiente p/ OCR externo) e `docker build` das imagens de worker (~700 MB com modelo); **benchmark de modelos flash + corpus real de anonimização pré-produção**; P2s do 06: `released=True` só após commit do atomic (anonymization/orchestrador), branch async de conflito sem teste direto, revisão humana dos 28 prompts; **K textual-informativo pendente de validação do dono**. P2s do admin-local: replicação do form no pre-check do /admin (sincronizar em upgrades do Django), `_wrapped` privado no teste de wiring, ADR-0004 fica histórico (0009 o emenda). P2s do 07: teste do ramo fail-closed do access, assertNumQueries da fila, card de decisões gateado por decision_event (rows são a fonte), re-render de POST inválido em corrida perdedora. P2s do 08: `scheduling_reopen_reason` persiste pós-reconfirmação e segue no card de agendamento (histórico vive nos eventos), mensagens default EN nos required do form de confirmação, duplicação de helpers de teste entre test_services/test_incident, teste de atomicidade simula corrida via validação concorrente. P2s do 09: casos de negativa total não aparecem mais na aba "decididos" do doctor (vão direto a FINAL_REPLY_POSTED — decisão de produto a confirmar com o dono), 3ª cópia do _CATALOG_ORDER (closure.py), `_delete_files_best_effort` captura só OSError, highlight da resposta final com teste reforçado pelo parent, enqueue do reenvio em cascata no atomic externo (inline=True = desvio conhecido), ordem de validação do reenvio criador-antes-de-estado (superior ao narrado). P2s do 10: mapeamento SDK→LlmError duplicado entre vision.py e OpenRouterClient (drift possível; testes só no pipeline), sem `select_for_update` por anexo (execuções sobrepostas do mesmo caso poderiam duplicar dispatch — retry sequencial coberto), fallback do badge "unknown" p/ processed sem match é inalcançável, upload suplementar pós-criação e supressão auditável de anexo fora de escopo (existem no ats-web). Quirk de harness documentado: flush de TransactionTestCase + --reuse-db apaga seeds de migration (mitigado com teste chamando a função da migration + fixtures get_or_create); após mudar migration rodar `pytest --create-db` uma vez. P2s do 11: ícones "any" com contraste baixo em superfície escura (ats-web tem variante -contrast; decisão de design), drift de regeneração dos PNGs não detectado pelo --check (só presença), cache.addAll tudo-ou-nada no install do SW (asset futuro ausente = SW nunca ativa silenciosamente), fallback offline de navegação inerte por decisão (HTML autenticado nunca em cache), read_at gravado antes do guard do destino (destinos nir/scheduler seguros; doctors não recebem notificações), notificações sem registro no Django admin, dashboard sem cache/export (contagens ao vivo). P2s deferidos do 18: teste de autoria do scheduler valida trio ator+papel+timestamp de forma agregada (endurecer por item). Próximo: arquivamento do 11 (fim do roadmap de 11 changes — pendências pré-produção no topo desta nota).

## Roadmap — 11 changes (resumo)

| # | Change | Conteúdo vertical |
|---|---|---|
| 01 | `bootstrap-django-hmd-core` | Scaffold, settings, docker-compose, accounts (papéis/multi-role/guard nir-only), templates base, AGENTS/PROJECT_CONTEXT, ADRs 0001–0003, seed_admin |
| 02 | `ad-kerberos-authentication` | `ad_upn`, KerberosBackend (minikerberos), failover, códigos, rate-limit, login CPF+senha, ADR |
| 03 | `case-core-fsm-procedures` | Case/CaseProcedure/CaseEvent/locks/comunicações, FSM django-fsm-2 (17 estados), catálogo 13 tipos + exam profiles |
| 04 | `intake-nir-upload` | Upload multi-PDF, worker pdf (PyMuPDF), regulation gate adaptado, declaração de tipos, meus casos |
| 05 | `presidio-anonymization` | App anonymization, pré-extração determinística, pseudônimos, worker, fail-closed, recognizers BR, ADR |
| 06 | `llm-pipeline-per-type` | Cliente OpenRouter, schemas por tipo + composição união, LLM1/LLM2, reconciliação, policy consultiva, prior-case, prompts versionados, ADR |
| 07 | `doctor-queue-decision` | Fila + filtro por subtipo, presenter re-identificado, alertas da policy, decisão por procedimento |
| 08 | `scheduler-multi-unit` | Fila, confirmar/desmarcar com unidade 1\|2, resposta final unidade 2 ao NIR, intercorrência (unidade 1) |
| 09 | `nir-result-closure` | Resultado final, confirmar recebimento → CLEANED, casos encerrados, reenvio corrigido |
| 10 | `attachment-processing-ocr` | OCR híbrido (local/visão), anonimização de anexo, verificação patient-match |
| 11 | `dashboard-notifications-pwa` | Dashboard por tipo/unidade, notificações in-app, PWA (ícone HMD), manual de usuário |

## Estrutura de alto nível (atual)

```
config/            settings por ambiente (base/dev/prod/test), urls, wsgi/asgi
templates/         templates raiz + accounts (login/perfil/home/switch-role) + admin/base.html (override sem link de senha); apps/accounts/templates/ (widget de senha, nível do app)
static/            CSS/JS (tema hospitalar HMD, Bootstrap 5.3 CDN)
tests/             suíte raiz (smoke + resolução de banco)
apps/accounts/     User/Role (ad_upn), backends (Kerberos AD + break-glass), kerberos.py (cliente/failover), ratelimit.py (anti-lockout), middleware (papel ativo + guard), admin, ad_check
apps/cases/        procedure_catalog (14 tipos/S1–S8, neurointerv→S2/neuro desde 2026-11-22), Case+FSM (django-fsm-2, 17 estados, +bypass_pipeline_divergence), CaseEvent/CaseProcedure/CaseDocument, procedures.py (declaração+upsert detecção), locks.py, communications.py+signals, event_trail.py (builder+rótulos/badges da trilha compartilhada) + templates/cases/_event_trail.html
apps/intake/       upload/declaração, pdf_utils (PyMuPDF), regulation_gate (SESAB), tasks (cluster pdf), meus casos/detalhe/gate
apps/anonymization/ deterministic, recognizers BR (checksum), engine Presidio singleton, services (núcleo+wrapper), tasks (cluster anonymization, fail-closed; release+transição no mesmo atomic), reidentify (+reidentify_structure recursivo), benchmark
apps/pipeline/      llm.py (cliente OpenRouter+erros), schemas/ (base+ blocos+união), llm1_service, llm2_service, procedure_reconciliation, policy, prior_case, ptbr_language_guard, json_parser, orchestrator+tasks (cluster llm) + signals (trigger duplo)
apps/llm/          PromptTemplate versionado (constraint parcial 1-ativo/nome), prompts_seed (28), services (build_case_prompts), seed_prompts
apps/doctor/       fila por estado+subtipo (Paginator), access.py (matriz por papel ativo), presenters (re-identificado), forms (decisão por procedimento), views (fila/detalhe/decide/PDF)
apps/scheduler/    services (confirmar/negar/intercorrência c/ resposta final ao NIR por unidade), fila (abas, Paginator), presenters (detalhe limitado c/ one-liner re-identificado), forms, views (fila/detalhe/ações/PDF por position)
apps/cases/closure.py  post_doctor_denial_reply (negativa → resposta final) + acknowledge_case_receipt (ciência + limpeza/minimização transacional)
docker-compose*.yml PostgreSQL 17 dev/test (+ docker/init.sql unaccent/pg_trgm)
docs/adr/          decisões arquiteturais (0001–0008)
```

## Regras não negociáveis

- **Identidade reservada SAR–HMD (ui-parity-foundation D1, 3 tokens):** o nome exibido é `SAR – HMD` (`APP_DISPLAY_NAME`/manifest/` <title>`; override por ambiente preservado) e os três tokens reservados — (1) primária `#0d4a8a` (header/`theme-color`/botões), (2) `short_name "HMD"`, (3) sigla `HMD` no ícone (com faixa geométrica de família SAR + variante `-contrast`) — SHALL permanecer os diferenciadores do HMD: nenhum alinhamento visual futuro SHALL alterar esses três tokens. Premissa externa: o rename espelho do ats-web (`SAR – CHD`) é de outro repositório.
- **Harness `verify-hmd` (ui-parity-foundation D7/D7b):** prova oficial dos slices visuais (fora do quality gate) — instância isolada em loopback sob `config.settings.verify`, banco próprio `hmd_verify_*` com checagens fail-closed, seed sintético (`seed_verify`) e backend sintético exclusivo (`backends_verify`, recusa carregar fora de verify), drive Playwright pinado com login real pelo form (cookie injetado nunca é prova) e evidência em `tmp/verification/`; mapas em `.pi/skills/verify-hmd/features/`.
- **Sem troca de senha no aplicativo (ADR-0011):** nenhuma rota, formulário, link
  ou instrução de troca/redefinição de senha — no perfil nem no Django admin
  (as três rotas herdadas são filtradas por nome). Senha do AD é presencial na
  TI; senha do admin local via `manage.py changepassword`. Ressalva aprovada:
  campos de senha do formulário de CRIAÇÃO de usuário (identidade local).
- Monolito Django SSR; sem DRF/SPA; sem e-mails transacionais (ADR-0003).
- Guard de intranet apenas para o papel ativo `nir`, decidido por papel ativo
  em sessão (ADR-0002).
- Policy clínica sempre consultiva — recomenda + explica, nunca bloqueia.
- Anonimização Presidio **antes** de qualquer chamada LLM (fail-closed).
- Settings por ambiente; segredos por env; `prod` falha fechado.
- Quality gate local (ruff + mypy + pytest) é o contrato de verificação.

## Quality bar

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy . && uv run pytest
```

Projeto de referência (padrões, somente-leitura): `/projects/dev/ats-web`.

## Fundamento do cabeçalho SESAB — CONCLUÍDO (change 22 `sesab-header-extraction`, 2026-09-15)

Descoberta do dono + verificação no corpus real: o cabeçalho padrão SESAB
(repetido por página) traz Código, Abertura, Dias em tela, Data Adm. Unid.,
Dias Unid., **Paciente (nome)**, Idade, Sexo, Raça/Cor, Nome Social (pode
estar em branco — NÃO é campo de captura do nome) e CNS (pode estar em
branco); **não há data de nascimento**. O valor de `Paciente:` desalinha na
extração linear (fica na linha de demografia antes do rótulo) — o nome ia
ao LLM em claro. Change arquivado: âncora da linha de demografia canônica
(Idade+Sexo+Raça/Cor juntos, enum IBGE fechado) + `Paciente:` sozinho na
linha seguinte; metadados `patient_age`/`patient_gender`/`patient_race`/
`days_on_screen` (migration 0010; worker pdf; zerados no reenvio;
preservados no CLEANED); `social_name` como candidato PESSOA próprio (sem
linkage, captura mesma-linha); tokenização de todas as ocorrências
(`<PESSOA_1>`; vazamento real fechado — E2E no PDF do dono: 84/F/PARDA/6
dias, 0 ocorrências restantes). Suíte 1330; benchmark NER on 63/63.

## Fecho do change ui-parity-foundation (slice 005, 2026-11-13; ARQUIVADO em 2026-11-13)

Páginas de sessão no padrão, fechando o change (slices 001–004 já verdes): **login** com toggle de senha funcional (`static/js/password_toggle.js`, adaptado do molde ats-web; `input-group` + `toggle-password`), aviso de acesso transitório mantido e **sem** reset/esqueci-senha ou e-mail (ADR-0003/D9, permanente); **notificações** com referência do caso (`Case.display_reference`), leitura individual por item (novo POST mínimo `notifications/<id>/read/` → `notification_read`, marca só ela e volta à lista; `notifications_open` segue marcando+redirecionando ao caso), marcar-todas, voltar e polling do sino (`bell_poll.js` sobre `unread-count`); guard de destinatário nas duas rotas (alheia = 404 sem marcar); **perfil** com `h1.page-title` no topo + cards + voltar; **manual** com seções por papel em cards, imprimível, texto instrucional preservado (diff de texto extraído idêntico — só marca/markup). Mapa `verify-hmd` executado nas telas do change (login toggle, perfil/manual, jornada 3→2→0 + 404 alheio — features `sessoes.md`/`notificacoes.md` + `drive-session-pages.mjs`, fora do gate). Gate verde: ruff + format + mypy + pytest (1628 testes). Próximo: changes 2–3 sobre esta fundação (débito D4: semântica da aba `Decididos`).

## Fecho do change ui-parity-queues (slices 001–003, 2026-11-14; ARQUIVADO em 2026-11-14)

Filas no padrão ats-web sem mudar regra de negócio: **fila médica** em `case-card + summary-box + pulse-dot + waiting-time`, abas `Pendentes/Decididos` (rótulo `Decididos` fiel à query por estado — **decisão B do dono**, débito do change 1 encerrado; sem recorte de data), endpoint `doctor:queue_partial` + htmx 20 s com reaplicação de busca/filtro, busca client-side, filtro server-side por subtipo autoritativo; **fila do agendador** no mesmo padrão (`Pendentes/Processados`, sem diferenciação por unidade); **meus casos** com cards, filtros server-side AND (`q` ≥3 chars, status, tipo), abas `Ativos/Encerrados` com contadores, partial + auto-refresh (sem paginação — a view materializa a lista inteira); blocos legados `nav-tabs` removidos das filas (navegação = `_nav` do change 1), pills NIR mantidas; badges contam na navegação, não no polling (D8); partial reusa o assembly com a expiração lazy de leases preexistente (D9, testada via partial). Mapa `verify-hmd` estendido (3 jornadas). Gate verde: ruff + format + mypy (276 arquivos) + pytest (1662 testes, TEST_DB_PORT=55435 nesta estação). Próximo: change 3 `ui-parity-intake-detail` (upload drag-drop, combobox, detalhe/decisão e painel no visual).

## Fecho do change ui-parity-intake-detail (slices 001–004, 2026-11-14; ARQUIVADO em 2026-11-14)

Últimas superfícies no padrão ats-web sem mudar regra de negócio: **painel** com toolbar de período (`.metrics-period-card/.metrics-period-toolbar`, 4 períodos HMD com `is-active` e preservação de filtros da lista), 6 cards de métrica (`.metrics-summary-card`) e lista de casos em `.case-card` (molde `_case_list.html` do ats-web) — **queries, filtros e fontes imutáveis intactos** (D4: diff de SQL/resultado zero; suíte de métricas verde sem alteração de asserts — prova R2). **Detalhe/decisão/confirmação** (slice 003): tabelas de identificação, blocos de relatório, badges e layout 2 colunas; formulários, ordem clínica, guards e atomics intactos. **Upload NIR** (slice 001): `upload-zone` com drag-and-drop + preview (arquivos, contagem, tamanho total) sobre os fields HMD, lote homogêneo e seção de anexos preservados; reenvio corrigido com o mesmo widget. **Combobox de procedimento** (slice 002): select canônico + combobox pesquisável (labels + códigos dos 13 perfis, sem acentos, teclado, ARIA) com progressive enhancement. Mapa `verify-hmd` estendido (4 jornadas: `upload-dragdrop`, `combobox-procedimento`, `detalhe-papeis`, `painel-visual`). Gate verde: ruff + format + mypy (281 arquivos) + pytest (1703 testes, TEST_DB_PORT=55435 nesta estação). Próximo: — (paridade visual concluída; archive sem mudança de comportamento).

## Fecho do change pwa-icon-simplify (slice 001, ARQUIVADO em 2026-11-15)

Ícones no molde do CHD: sigla HMD branca centralizada sobre o fundo plano `#0d4a8a` (sem a faixa de família), variante `-contrast` sem faixa; geometria do texto espelhada do ats-web (170px, tracking 10, baseline 308.44, x compensado 261.57) após revisão do dono apontar texto pequeno e descentralizado; PNGs regenerados deterministicamente; spec `pwa-manual` MODIFIED (sem marcador de família). Gate verde: ruff + format + mypy + pytest; `validate --strict` ok.

## Fecho do change intake-batch-semantics (slices 001–003, ARQUIVADO em 2026-11-14)

Semântica central do envio corrigida antes da fase 2 (decisão do dono, molde ats-web): cada PDF é um relatório de um paciente e vira um caso independente (`submit_report_batch`: guard fail-closed → tipo único → limites 30 arquivos/20MB/100MB → anexos×pdf_count → loop com transação por caso e erro nomeado por arquivo); `procedure_type` único por lote; anexos só com exatamente 1 PDF (multi = casos sem anexos + erro não-bloqueante; único + inválido = aborta); reenvio corrigido e gate de documentos com exatamente 1 PDF. Deltas reconciliados na main (`intake-nir` REMOVED Criação/ADDED Envio em lote + MODIFIED Revisão do gate; `case-closure` MODIFIED reenvio 1 PDF + tipo único; `attachments` MODIFIED) — semântica já vigente no código, specs alinhadas no archive. Gate verde: ruff + format + mypy + pytest (1705 testes, TEST_DB_PORT=55435 nesta estação); `validate --strict` ok.

## Fecho do change case-detail-human-reference (slices 001–005, ARQUIVADO em 2026-11-14)

Referência humana em toda a UI (nº de ocorrência no lugar do uid): properties `short_id`/`display_reference`/`disambiguated_reference` no `Case`; detalhe do NIR com card de identificação; flashes e títulos com referência humana; sufixo de desambiguação em meus casos com nº duplicado; badge `prior-case` com referência nº · <8 chars (URLs seguem com uid como chave técnica; linhas rotuladas mantêm `—` sem nº). Suíte 1516→1553 na implementação. P2s adiados: teste HTTP do flash do ramo de divergência, R2 do my_cases pina só a linha do nº, badge de prior-case sem nº apenas type-required. Deltas reconciliados na main (`intake-nir` MODIFIED Meus casos + 4 cenários de filas do change 2; `doctor-decision`/`scheduling` preservados). Gate verde: ruff + format + mypy + pytest (1705 testes); `validate --strict` ok.

## Fecho do change operational-lists-ui-parity (slices 001–005, ARQUIVADO em 2026-11-15)

Listas operacionais aproximadas do molde ats-web sem alterar FSM, guards, regras clínicas, escopo, ordenação, paginação, locks ou polling. **Médico**: pendentes com página/container alinhados, filtro único por subtipo com contagens apenas nessa aba, cards 8/4, demografia, espera única e ações de reserva; decididos sem recorte de data nem espera, com disposição e data/hora por procedimento, decisões mistas aprovado/negado sem agregação e CTA «Ver detalhes». **Agendador**: filtro server-side por tipo antes da paginação, cards 8/4 com decisões por procedimento, espera única, unidade quando definida e reservas; aba processada preservada. **NIR**: Meus casos com título «Todos os seus encaminhamentos», navegação Ativos/Encerrados apenas no header com `filter_qs`, filtros AND/escopo/partial intactos, cabeçalho neutro com uma contagem e cards compactos 4/3/3/2 com exceções e vazios acionáveis. **Navegação**: pills com tokens HMD, aba ativa branca + primária, hover/focus, alvos ≥44 px e contadores exclusivamente por `data-count`/`::after` (zero oculto), sem badges inline ou contagens duplicadas. Identidade SAR–HMD e primária `#0d4a8a` preservadas.

Deltas reconciliados nas specs principais: `doctor-decision` substituiu a requirement da fila pela operacional (22 cenários), `scheduling` ampliou a fila completa (19) e `intake-nir` atualizou Meus casos/detalhe (17); `validate --specs` 15/15 e `validate --strict` do change aprovados. Gate final verde: ruff + format + mypy (282 arquivos) + **1760 testes** (`TEST_DB_PORT=55435`). Regressão `verify-hmd` na árvore final: header 180 checks, genérica/fila-médica 17, pendentes médico 60, decididos 55, agendador 85, meus casos 62, detalhe 34 e upload 72 — todos sem falhas, com PNG/snapshot/JSON, medidas 1280/390 e cleanup. Relatório consolidado: `/tmp/hmd-slice-oli-005-report.md`. Próximo: — (change arquivado; nenhum novo escopo autorizado).

## Fecho do change pdf-original-viewer-parity (slices 001–007, ARQUIVADO em 2026-11-16)

Paridade ats-web de visualização do PDF original nos 4 papéis, sem mudar guards/FSM/regras: dois cards colapsáveis
(`Texto Extraído do PDF` — omitido sem texto no NIR/agendador/painel, placeholder no médico — e `Visualizar PDF
Original`) com **embed lazy por documento** (`data-pdf-doc-collapse` + `data-src` sem `src`; gate por breakpoint
`matchMedia(768px)`, resize carrega o aberto, mobile nunca inicia busca; fallback `<noscript>`), **viewer mobile
dedicado** (PDF.js vendored v4.0.379, Voltar topo/rodapé, `?next=` seguro via `resolve_safe_next_url`),
rotas de serve/viewer por papel **espelhando os guards dos detalhes** (papel errado→403; alcance/inexistente/sem
arquivo→404; **decisão do dono**: médico fora-da-matriz→403 consolidado na spec) e **anexos visualizáveis**
(serve/viewer por papel, só-PDF no viewer, JPEG `img loading=lazy`, seção própria — NIR/médico/painel).
Execução em 7 slices com loop worker(muse-spark)+reviewer(gpt-6.1-terra) controlado pelo parent: 2 timeouts de
runtime com retry único, 3 decisões do dono (opção C de evidência — fetch provado DIRETAMENTE no headed-Xvfb,
C ficou só p/ renderização; waiver de rodada 3 no 002; 403 médico), falhas de host-gate cobertas por
verificação parent. Harness endurecido: **MEDIA_ROOT por run** (`HMD_VERIFY_MEDIA_ROOT` + manifest + guardas
adversariais traversal/symlink/igualdade no control-hmd e drive), contador de rede por pathname exato
any-status com probes excluídos, fixtures VERIFY-101/102/103 por jornada via runVerifyPy (seed intocado),
jornada VERIFY-001 restaurada, gates pré-goto fail-loud. Gate final **1958 testes** verdes; specs 16/16
(`pdf-original-viewer` NOVA + scheduling/dashboard/attachments MODIFIED). Débitos P2 registrados nos
checkpoints (`/tmp/pvp-loop/...`): contador any-status no drive-detalhe, rótulo mobile NIR N>1, asserções
parser-HTML, paridade serve arquivo-vazio, guardas estruturais PVP-001. Commits b110c2a→57a5e05.

## Fecho do change watermark-strip-hardening (slices 001–002, ARQUIVADO em 2026-11-17)

Endurecimento da limpeza de marca d'água do intake com **premissa de domínio corrigida** (a marca quase
nunca coincide com o nº de ocorrência — suposição antiga persistida nas fixtures; mesmo relatório de página
única tem ≥2 linhas de marca): nomeação unificada por **linha de marca** (linha composta exclusivamente de
1..N repetições do token de 5–6 dígitos — cobre banda cheia, diagonal parcial do molde sarv3 e linha solta)
com limiar `_MIN_MARK_LINES = 2`; **sweep assimétrico** (token coincidente com o nº é varrido globalmente via
`\b`; token distinto sai só das linhas inteiras — valores clínicos inline de 5–6 dígitos preservados);
`extract_document_text` junta páginas com `\n` explícito (endurecimento de contrato, testado via dublê do
`pymupdf.open`); `strip_watermark(text, *, record_number=None) -> WatermarkStripResult` com
`WatermarkReport(tokens/mark_lines/swept_occurrences)` (semântica fechada D3) e **telemetria no
`CASE_EXTRACTION_COMPLETED`** sob `watermark` via `dataclasses.asdict` — só contagens, jamais valores de
token na telemetria (o `record_number` pré-existente permanece, inclusive no caso coincidente: decisão de
contrato do P1 da revisão do 002). Tamanho 5–6 dígitos e ordem bruto→strip (D3/R5b) inalterados. Execução em
loop worker(muse-ember-1.0:max)+reviewer(gpt-6.1-terra) controlado pelo parent: P2 do 001 (pin de linhas
soltas 3×) e P1 do 002 (escopo da proibição de valores) resolvidos com decisão documentada; incidente de
despacho (prompt vazio → slice 002 fora de ordem) contido via stash, nada commitado indevido. Gate final
verde: ruff + format + mypy (303 arquivos) + **1970 testes** (`TEST_DB_PORT=55435`). Spec `intake-nir`
sincronizada (requirement Extração assíncrona: 49 cenários no total); `validate --specs` ok. Commits
12630dc→6936a72.

## Fecho do change retencao-auditoria-casos-encerrados (slices 001–003, ARQUIVADO em 2026-11-17)

Decisão do dono (fase de estabilização): o encerramento RETÉM tudo (7 campos clínicos/LLM + rows/arquivos de documentos/anexos) para benchmark com corpus real e auditoria supervisora — política temporária do ADR-0010 (base jurídica: execução íntegra no perímetro do hospital com autorização expressa dos gestores; gatilho de reversão: versão 1.0; responsável: gerência de TI; runbook flip→`--dry-run`→purga→registro com E2E dev validado e registrado). **Slice 001**: flag `CASE_CLOSURE_MINIMIZATION_ENABLED` (default `false`=retenção, passthrough no `web` do compose prod) + gate de modo NOS entry points do closure (função interna `_clean_acknowledged_clinical_data` INCONDICIONAL — reusada pela purga; `test_header_persistence` verde sem pino); worker de anexos blindado (gates `CLEANED` + transações curtas com `select_for_update` + **dispatch durável** — lock+gate+evento `DISPATCHED` no mesmo atomic com commit ANTES do envio + guard por página 2..N — e verificação com API pública única `request_verification_outcome`/`VERIFICATION_FAILURE_EXCEPTIONS`; janela residual commit→envio documentada na D8); textos/trilha verdadeiros nos dois modos (rótulos CLEANED "Caso concluído"/CLEANING "Disposição dos dados em andamento"). **Slice 002**: card «Sumário apresentado ao médico» no detalhe do painel (manager/admin) — builders extraídos para `apps/cases/presentation.py` (API única `build_structure_sections`; paridade médico×painel provada por teste), re-identificado sem tokens, cobre casos ativos e `CLEANED` retidos (placeholders independentes por bloco). **Slice 003**: purga retroativa — serviço `purge_closed_case_data` em `closure.py` (`atomic(durable=True)`, commit real obrigatório, deleção física síncrona pós-commit) + comando `purge_closed_case_data [--dry-run]` (só `CLEANED` com dados, idempotente, atomic por caso, exit não-zero com falha física) + **trilha em duas fases** (`CASE_RETAINED_DATA_PURGED` atômico com a limpeza + `CASE_RETAINED_DATA_PURGE_OUTCOME` com badge de perigo quando `files_failed>0`; payload/log/saída sem PHI). Execução em loop worker(muse-ember-1.0:max)+reviewer(gpt-6.1-terra:high) controlado pelo parent (11 rodadas ao todo; desvios de boundary para `extraction.py`/`verification.py` autorizados e registrados). Suíte 1972→2019. Specs: `case-closure` (requirement renomeado p/ «encerramento e disposição de dados» + MODIFIED ×2 + ADDED purga — 6 reqs) e `dashboard` (ADDED card — 6 reqs); `validate --specs --strict` 16/16. Débitos P2/P3: seleção da purga varre todos os `CLEANED` com `exists()` por caso (volume grande → mover p/ queryset `Exists`); badge do outcome neutro só muda com `files_failed` int>0 (defesa de apresentação); janela residual commit→envio do OCR (D8, alternativas rejeitadas registradas).

## Fecho do change confirm-nir-procedure-review (slices 001–004, ARQUIVADO em 2026-11-20)

Confirmação autoritativa de procedimento pelo NIR de ponta a ponta (4 slices, loop controlado pelo parent com
worker/reviewer fresh por fase; 001 com muse-ember-1.0/gpt-6.1-terra + decisões HD-001/002/003 documentadas em
`/tmp/pi-openspec-loop-*`; 002–004 com muse-spark-1.3-contributor:high/:max; 43+12+22 novos testes, suíte
2031→2210, commits c9b2420→7ffafff). **Slice 001 (domínio/pipeline)**: marcador prospectivo
`CASE_PROCEDURE_REVIEW_ENABLED {version:1}` apenas no atomic da primitiva única de criação (lote/reenvio novos;
originais/históricos nunca convertidos); validação de autoridade/fonte/texto anonimizado/mapa por digests `v1:`;
confirmação com declaração anterior vs confirmada (manter/trocar de um dos 13 tipos, incl. não detectado),
justificativa 1–500 e confirmação de leitura obrigatórias, atomicidade durável com lock de linha e lease;
invalidação irreversível auditada (restaurar valores não revive autoridade; integridade atual prevalece sobre
motivo gate histórico); reprocessamento REAL de LLM1+reconciliação+policy+prior-case+LLM2 sem reextrair PDF;
`APPLIED.automatic_procedures` = detecção exata canônica da nova passada (nunca união/conflitos); guarda de
completude compartilhada (pública e sob lock final) com falha fechada; transição protegida autorizada
`LLM_SUMMARIZING→LLM_EXTRACTING` (HD-001) descarta resposta estagnada em voo e refaz reconciliação automática
com idempotência de eventos equivalentes; integridade revalidada em TODAS as fronteiras (pré-dispatch, pós-chamada,
lock de persistência, transição final). **Slice 002 (UI NIR)**: card de revisão no detalhe (select único dos 13
com valor atual pré-selecionado, cardinalidade via `getlist`, submit sem JS), negativos endurecidos sem mutação
(CSRF/papel/404 alheio/revisão obsoleta 400/bytes alterados/fonte ilegível/lock 409) com mensagens sem PHI,
flash com referência humana; adaptador determinístico exclusivo de verify `config/llm_verify.py` (D9: zero rede,
`LlmError` tipado em falha/desconhecido, guardas dev/test/prod incl. programáticas) + `seed_verify
--procedure-review` (prompts primeiro, fixtures 990101–990104 idempotentes antes/depois de confirmação real,
conflito explícito); jornada verify-hmd 37/37. **Slice 003 (recuperação de dispatch)**:
`ProcedureReviewEnqueueAfterCommitError` (case/confirmation/recovery_scheduled/reason_code); callback único
`on_commit(robust=False)` no signal do pedido; falha de enqueue preserva a confirmação e agenda `Schedule.ONCE`
+60 s no cluster `llm` (nome caso+confirmação, savepoint, recuperação+auditoria commitadas em transação própria
ANTES da exceção propagar; atomic inteiro falho → `recovery_scheduled=False` com log sanitizado sem alegação);
view trata 302-aviso (programada) vs 503-suporte (não programada), sem retry/PHI; dedupe por nome incl. corrida;
UUID serializado executa recuperação real sem PDF e sem duplicar efeitos; smoke qcluster `llm` real em verify.
**Slice 004 (evidência ao médico + integração final)**: aviso consultivo do `APPLIED`+`CONFIRMED` referenciado
(confirmado, mantido/trocado, declaração anterior, detecção ORIGINAL da revisão vs NOVA exata — incluindo vazia
explícita, nunca união —, autor/data/justificativa escapadas, ressalva clínica); sem aviso fictício em
automático/legado; auditoria legível correlacionada no painel (`event_detail_lines` + escaping SSR provado);
declaração efetiva governa subtipo/singleton com decisão clínica livre de veto (prova HTTP novo/antigo subtipo,
generalista, admin); guarda de completude no GET e POST; retido/FAILED nunca prontos na fila; jornadas reais
manter/troca/vazia/erro com fábrica D9 reutilizada, zero transporte externo, 390/1280. **Specs promovidas**:
case-management +2 (8 reqs), doctor-decision +2 (9), intake-nir MODIFIED Revisão do gate + Meus casos e ADDED
Confirmação explícita + Prospeção apenas novos envios (7), llm-pipeline MODIFIED Reconciliação/Sumarização/
Processamento e ADDED Integridade no reprocessamento + Recuperação observável de enqueue (9); `validate --specs`
16/16. Pré-release: smoke operacional completo do qcluster com workers/modelos de produção (bloqueio explícito
dos relatórios 003/004); P2s deferidos registrados nos receipts de execução.

## Fecho do change add-neurointervencao-procedure (slices 001–002, ARQUIVADO em 2026-11-22)

**14º tipo de procedimento: `neurointerv` (Neurointervenção — embolização de aneurisma ou malformação
vascular intracraniana)**, fechando a lacuna do subtipo `neuro` (plano inicial dizia
"neuroradiologia/neurointervenção", mas o catálogo só tinha `art_cerebral` diagnóstico; auditoria dos
archives não achou decisão sobre o tipo — a menção de 2026-09-07 no slice-001 R5 tratou-a como categoria
anestésica). **Decisão do dono (2026-11-22)**: critérios da seção **S2** (a mais restritiva existente) por
referência — sem S9, sem threshold novo; suporte anestésico True (lista do parametrosHMD); subtipo `neuro`.
Plano revisado por reviewer readonly independente (NEEDS-REWORK: 3 BLOCKERs + 2 MAJORs incorporados —
fusão das pinagens no slice 001 com gate completo obrigatório, inventário estendido enum 14/CATALOG_SIZE/
`state["prompts"]`, texto visível do card "14 tipos do catálogo", grep ampliado, runbook). Execução: 2 loops
pi-subagents-openspec-loop/2 (worker muse-spark-1.3-contributor:high + reviewer gpt-6.1-terra:high;
evidências `/tmp/hmd-nip-001.ZyxjBt` e `/tmp/hmd-nip-002.bF2D1J`; slice 001: 1 ciclo de fix — P1 label
não atrelado à opção no select de revisão, corrigido com parser (value,label,selected) e asserção exata;
slice 002: review limpa na 1ª rodada). **Slice 001 (1396eb0)**: perfil pós-`art_cerebral`, EXPECTED_PROCEDURE_COUNT=14,
S2 por referência, seeds `proc.neurointerv.llm1/llm2.user` com frase literal, contagens 31 em todos os pontos
(llm ×6 + accounts + help), fixações enum/CATALOG_SIZE, pinagens de selects/roteamento (neuro vê, cardio
não vê, multiprocedimento)/detecção sem bloco específico, texto do card de revisão. **Slice 002 (7cdb132)**:
varredura residual grep 38→8 (remanescentes = 1 aritmética correta + 1 nota histórica + 6 ADRs arquivados),
runbook passo 5 obrigatório em releases "sem migrations, MAS com seeds" + convenção do changelog, gate
completo. Suíte 2331→**2339** no branch de origem (2377 no main pós-merge com transport-badge; CHANGELOG
**0.1.26** — 0.1.25 já lançado como transport-badge). Specs promovidas: case-management (RENAMED "Catálogo de 13 tipos"→"Catálogo
de tipos de procedimento do HMD" + MODIFIED 14 tipos/cenário de roteamento; Purpose ajustada no archive) e
intake-nir (MODIFIED "14 tipos do catálogo"); validate --specs 17/17. **Deploy**: sem migrations, MAS com
seeds — passo 5 do runbook obrigatório (cria os 2 templates novos; pulado → 1ª chamada de prompt do tipo
falha). Pendências/P2 de backlog: pinagem explícita `evaluate_preop_policy("neurointerv")`→S2; label de
78 chars conferir em mobile 390px no próximo smoke verify-hmd; `seed_prompts` local depende de PG dev com
credenciais default (auth falhou no ambiente do loop — idempotência coberta por testes).

## Backlog pós-piloto (fase 2 — dono, 2026-09-15, E2E dev validado)

Cada item abaixo é um change próprio (dono enumerou após validar o fluxo
ponta a ponta em dev). Referência de padrão: `/projects/dev/ats-web`. O
fundamento do cabeçalho (acima) já fornece nome/idade/sexo/raça/dias-em-tela
— os changes abaixo agora têm dados para consumir.

1. ~~Cards das filas~~ **CONCLUÍDO** (change `queue-cards-wait-time`,
   2026-09-15): filas de médico/agendador ordenadas por `days_on_screen`
   desc nulls-last (tempo oficial; desempate FIFO — molde ats-web) com
   cards nome+idade (`0 a` válido), ⏱ Aguardando há/Recebido há por aba,
   data absoluta e badge `N d em tela`; meus casos do NIR com nome+idade
   (escopo por criador; painel zero-PHI intacto).
2. ~~Detalhe do caso (médico): demografia~~ **CONCLUÍDO** (change
   `doctor-detail-context`, 2026-09-15): card de identificação com Idade
   (`84 a`)/Sexo/Raça-Cor (`—` quando ausentes).
3. ~~Ordem dos cards no detalhe (médico)~~ **CONCLUÍDO** (mesmo change):
   quadro clínico (Procedimentos declarados → Sumário clínico → Estrutura
   extraída) antes de Alertas consultivos; Decisões registradas entre o
   quadro e o consultivo; ordem pinnada por índices no HTML.
4. ~~Trilha de eventos~~ **CONCLUÍDO** (change `painel-ats-parity`,
   2026-09-15): trilha com rótulos legíveis (EVENT_LABELS cobrindo 100% do
   enum, anti-drift) em card collapsible fechado no DETALHE do caso no
   painel; NIR/médico sem trilha (badge «Falha no processamento» em
   FAILED; decision_event médico preservado).

**BACKLOG PÓS-PILOTO CONCLUÍDO** (itens 1-4; 25º archive). Política de PHI
corrigida pelo dono: **zero-PHI é exigência do perímetro EXTERNO (LLMs/
serviços de terceiros — tokenização/guard)**; a UI interna (funcionários)
mostra identificação completa (painel incluído; métricas seguem contagens
por natureza). Release liberada para preparação (dono).
5. ~~Painel lista de casos + encerramento administrativo~~ **CONCLUÍDO**
   (change `painel-lista-encerramento` arquivado 2026-09-14; 3 slices;
   lista zero-PHI por nº de ocorrência, encerramento admin com minimização
   rows+arquivos, gates anti-worker-zumbi, 4º marco de notificação).
   Pendências de UI registradas nos desvios do slice 003 (status=CLEANED ×
   scope ativos; card compor "Agendado · Encerrado administrativamente").
