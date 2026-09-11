# Changelog

Formato: versões com resumo por change (Keep a Changelog adaptado ao workflow
OpenSpec — cada change tem proposal/design/slices/specs arquivados em
`openspec/changes/archive/`).

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
