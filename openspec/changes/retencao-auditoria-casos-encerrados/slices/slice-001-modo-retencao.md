# Slice 001 — Modo de retenção no encerramento (flag + gate + worker de anexos + textos + ADR)

**Pré-requisito**: nenhum (primeiro slice do change).

```yaml
expected_files:
  - config/settings/base.py
  - .env.example
  - docker-compose.prod.yml
  - apps/cases/closure.py
  - apps/attachments/tasks.py
  - apps/attachments/verification.py
  - apps/attachments/extraction.py
  - apps/attachments/tests/test_closure_worker_abort.py
  - apps/cases/tests/test_closure_ack.py
  - apps/cases/tests/test_administrative_closure.py
  - apps/cases/tests/test_closure_worker_abort.py
  - apps/intake/tests/test_closure_views.py
  - apps/intake/tests/test_my_cases.py
  - apps/dashboard/tests/test_views.py
  - apps/dashboard/event_labels.py
  - apps/dashboard/tests/test_event_labels.py
  - templates/intake/case_detail.html
  - templates/dashboard/admin_close_confirm.html
  - templates/dashboard/case_detail.html
  - templates/accounts/manual.html
  - docs/adr/ADR-0010-retencao-temporaria-encerramento.md
```

> **Desvio de boundary autorizado pelo parent (rodadas 3–4, 2026-11-17)**:
> `apps/attachments/verification.py` e `apps/attachments/extraction.py`
> adicionados ao `expected_files`. Motivos: a persistência gateada da
> verificação (D8) exige compor request→persist com gates entre eles — a
> alternativa sem editar os módulos (importar helpers privados + replicar
> o fluxo em tasks.py) cria duas versões do mesmo pipeline e acoplamento a
> API privada, rejeitada; e o OCR multipágina envia uma página por chamada
> — o guard de status precisa rodar antes de CADA página (review 3, P1),
> o que só é alcançável em `extraction.py`. `verification.py` expõe UMA
> operação pública de request/normalização/classificação
> (`request_verification_outcome` + `VERIFICATION_FAILURE_EXCEPTIONS` +
> persistências) consumida por tasks.py (com gates) e pelo próprio
> `verify_attachment`; `extraction.py` ganha o guard opcional por página
> sem conhecer `Case`/status (callable injetado por tasks.py).

## Contexto necessário

- `apps/cases/closure.py`: `_clean_acknowledged_clinical_data` (D2 do change
  09) zera os 7 campos (`_CLEANED_EMPTY_VALUES`) e deleta rows de
  documentos/anexos; os chamadores coletam nomes de arquivos ANTES do delete
  e deletam físico best-effort em `transaction.on_commit`.
  `acknowledge_case_receipt` (ciência NIR) e `administratively_close_case`
  (painel) são os dois entry points (e os únicos chamadores).
- `apps/attachments/tasks.py`: worker de anexos **sem gate de status** — a
  única proteção contra corrida com o encerramento é a deleção da row
  ("`DoesNotExist` → no-op", docstring L7-13); `_process_attachment` e
  estágios (extração/anonimização/dispatch de OCR externo/verificação) não
  releem `case.status`. Contrastar com intake/anonymization/LLM1/LLM2, todos
  com gate explícito testado em `apps/cases/tests/test_closure_worker_abort.py`.
- Rotas de serve de documento/anexo (intake/scheduler/doctor/dashboard) NÃO
  têm guard de status: o 404 de caso limpo vem da inexistência das rows.
- Textos que afirmam minimização e ficarão falsos em modo retenção:
  `templates/intake/case_detail.html` (~L193, ciência), 
  `templates/dashboard/admin_close_confirm.html` (~L29) e
  `templates/dashboard/case_detail.html` (~L227, encerramento administrativo),
  `apps/dashboard/event_labels.py` (`CASE_STATUS_CLEANED` → "Caso concluído
  e dados minimizados"), `templates/accounts/manual.html` (~L67/L124).
- Padrão de flag: `INTAKE_ENABLED`/`*_RUN_TASKS_INLINE` em
  `config/settings/base.py`; passthrough no `docker-compose.prod.yml`
  (ex.: `INTAKE_ENABLED: ${INTAKE_ENABLED:-false}` no serviço web).
- `apps/intake/tests/test_header_persistence.py` (~L375) chama
  `_clean_acknowledged_clinical_data` DIRETAMENTE — não recebe pino (ver D2).

## Goal

Encerramento (ciência e administrativo) com disposição de dados governada
por `CASE_CLOSURE_MINIMIZATION_ENABLED`: default `false` = RETENÇÃO total
(nada zerado/deletado), `true` = minimização original. FSM, eventos,
locks, guards, rotas e notificações intocados; worker de anexos com gate
explícito de caso encerrado; textos de UI/trilha verdadeiros nos dois
modos; flag cabeado em produção.

## Deliverables

### R1 — Settings + cabeamento (D1)

- `CASE_CLOSURE_MINIMIZATION_ENABLED = os.environ.get(..., "false")...` em
  `config/settings/base.py` (comentário: política temporária ADR-0010) +
  entrada em `.env.example` + passthrough no serviço `web` do
  `docker-compose.prod.yml` no padrão `INTAKE_ENABLED`.

### R2 — Gate nos entry points + worker de anexos (D2/D8)

- `_clean_acknowledged_clinical_data` permanece INCONDICIONAL (semântica de
  força total — reusada pela purga do slice 003). A decisão de modo mora
  NOS DOIS entry points: retenção ativa → pulam a limpeza E a
  coleta/deleção de arquivos; minimização ativa → caminho atual byte-a-byte.
  Transições FSM, eventos, release de lock, worker-abort por status e
  notificação do criador idênticos nos dois modos.
- `apps/attachments/tasks.py`: gates de status entre etapas + **transações
  curtas com `select_for_update` no `Case`** em cada ponto de decisão/
  escrita (status re-lido sob o lock na MESMA transação da escrita/evento,
  incluindo verificação em sucesso/falha e `_fail_attachment`) + **evento
  `DISPATCHED` durável**: transação curta (lock + status + evento) com
  commit ANTES do envio externo, após a rasterização (design D8 — sem
  transação longa durante envios, sem branch de compensação, trilha
  append-only preservada, janela residual commit→envio aceita e
  documentada).

### R3 — Textos e rótulo verdadeiros nos dois modos (D9)

- Templates de ciência/encerramento administrativo/manual: textos
  CONDICIONAIS ao modo (flag no contexto) ou neutros ("disposição dos
  dados conforme a política vigente — ADR-0010").
- `CASE_STATUS_CLEANED` em `apps/dashboard/event_labels.py`: rótulo
  renomeado para "Caso concluído" (verdadeiro nos dois modos).

### R4 — Testes (RED→GREEN)

- Novos (RED primeiro): ciência em modo retenção preserva campos
  populados/rows/arquivos (inclusive anexos processados); encerramento
  administrativo em modo retenção idem + evento de auditoria normal;
  serve de documento de caso `CLEANED` retido → 200 no intake (criador) E
  no painel (manager); corrida worker de anexos × encerramento
  administrativo (anexo `processing`, lease expirada, caso fechado → task
  é no-op, SEM dispatch externo, SEM evento de OCR — molde
  `test_closure_worker_abort.py`); textos condicionais renderizados por
  modo.
- Pinos `override_settings(CASE_CLOSURE_MINIMIZATION_ENABLED=True)` nas
  asserções existentes de minimização/404 (nenhum teste depende do default
  do ambiente). `test_header_persistence.py` segue verde SEM pino (prova
  do D2 — função interna incondicional).
- Bateria: `TEST_DB_PORT=55435 uv run pytest -q apps/cases apps/intake
  apps/dashboard apps/attachments` + suíte completa + gate completo do
  AGENTS.md.

### R5 — ADR-0010

- `docs/adr/ADR-0010-retencao-temporaria-encerramento.md`: decisão do dono
  (fase de estabilização), fundamento POR FINALIDADE (auditoria supervisora
  da assistência vs. benchmark com corpus real), escopo (7 campos +
  rows/arquivos de documentos e anexos; nada além), reversão (flag por
  ambiente + purga retroativa do slice 003 + runbook: flip → `--dry-run` →
  purga → registro de execução), gatilho de revisão e responsável.
- **Respostas do dono (2026-11-17) a registrar no ADR**: base jurídica =
  execução íntegra e exclusiva dentro do perímetro do hospital com
  **autorização expressa dos gestores** (cobre as duas finalidades);
  gatilho de reversão = produto alcançar a **versão 1.0**; responsável
  pela reversão e pelo acesso ao corpus retido = **gerência de TI do
  hospital**.

## Gates para o reviewer (2 linhas)

1. `git diff` de `closure.py` mostra a função interna de limpeza intacta e a
   decisão de modo apenas nos dois entry points (retenção pula limpeza E
   arquivos); nenhuma transição/evento/notificação alterada; worker de
   anexos com gate de status re-lido antes do dispatch externo.
2. Nenhuma asserção de minimização/404 depende do default do ambiente
   (pinos explícitos em todos os testes de modo), `test_header_persistence.py`
   passa sem edição (função interna incondicional), e os textos de
   ciência/encerramento/trilha são verdadeiros nos dois modos.

## Out of scope

- Card do painel (slice 002) e purga retroativa (slice 003).
- Qualquer mudança em FSM/rotas/guards/notificações, em
  `anonymization_report`, ou nas visões NIR/agendador do detalhe.
