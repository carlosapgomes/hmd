# Slice 006: LLM2 + orquestrador + cluster llm + ADR-0008

## Objetivo

O fechamento do pipeline: `llm2_service` (sumário+sugestão numa chamada, visão filtrada, **policy determinística prevalecendo**, strictest), orquestrador `process_case_pipeline` no cluster `llm` (trigger duplo com anti-recursão, retomada pós-bypass, lock, fail-closed), worker no compose e o ADR-0008. Fim-a-fim: `LLM_EXTRACTING → AWAITING_DOCTOR`.

## Contexto necessário (contexto zero)

- Slices 001–005 entregues: cliente/schemas/prompts/llm1+reconcile+bypass/policy+prior-case.
- Change 05 (padrões a replicar): signal com guarda anti-recursão (`payload["source"]`) + `transaction.on_commit`; task idempotente por estado com branch (resume); lock de worker com release no finally (e a coordenação transição+release no mesmo atomic quando o hook de saída é consumido por signal — ver fix do change 05: a transição que emite evento consumido por signal deve commitar junto com o release); `ALT_CLUSTERS`; flag inline.
- FSM (change 03 + slice 004): `start_llm_extraction` (self), `complete_llm_extraction` (→LLM_SUMMARIZING), `start_llm_summarization` (self), `complete_llm_summarization` (→AWAITING_DOCTOR), `fail_processing`, `bypass_pipeline_divergence`.
- Referências ats-web (SOMENTE-LEITURA): `apps/pipeline/{orchestrator,llm2_service_v2,summary}.py` — `_build_llm2_structured_data_view` (visão filtrada) e a regra "policy vence o LLM" + `strictest_global_support`.

## Requisitos

- **R1** `run_llm2_summarization(case, *, user=None, role="system")`: tipos = **reconciliados** (declarados — bypass preserva declarado); monta `visao_estruturada` = cópia efêmera do `structured_data` filtrada pelos tipos (padrão ats-web) + `policy_result` + prior-case summaries; **assert de tokens RECURSIVO** (payload serializado vs mapas de pseudônimos do caso E dos casos prévios — o motivo do prior-case entra pré-anonimizado pelo slice 005); prompts `llm2` (003); 1 chamada; validação strict (schema Llm2 do 002) + language guard + retry único (regime do 004); falha → levanta `LlmPipelineError("llm2_<motivo>")` (orquestrador é o dono do fail_processing). Sucesso: **reconciliação final** — por procedimento, `policy.recomendacao == recusar → sugestão = recusar(motivos_policy)` (LLM não suaviza); **agregado do caso = qualquer procedimento recusado ⇒ agregado recusa com motivos somados** (definição HMD explícita; `strictest_global_support` do ats-web era só p/ suporte anestésico e não se aplica — correção do review); persiste `Case.summary_text` + `Case.suggested_action` + evento `CASE_LLM2_COMPLETED` (resumo: sugestões por tipo, versions).
- **R2** (campos/eventos já criados no slice 004 — dono único; nada de migration aqui.)
- **R3** Orquestrador `process_case_pipeline(case_id)`: branch por estado — `LLM_EXTRACTING`: claim lock `worker_llm`/`system`; `start_llm_extraction`; **LLM1; reconcile**; divergência → R5 do 004 (retém, release, sai) | ok → `complete_llm_extraction`; continua em `LLM_SUMMARIZING`: `start_llm_summarization`; **policy; prior-case; LLM2**; `complete_llm_summarization` → `AWAITING_DOCTOR` (transições de saída com hooks → coordenação transição+release no mesmo atomic, padrão change 05). `LLM_SUMMARIZING` (retomada pós-bypass): pula LLM1/reconcile (reaproveita artefato), roda policy/prior/LLM2/complete. Demais estados → no-op com log. Exceção → refresh + `fail_processing(tipo)` → `FAILED`. Release no finally.
- **R4** Cluster/settings: `Q_CLUSTER["ALT_CLUSTERS"]["llm"] = {workers: 1, timeout: 900, retry: 960}`; `LLM_RUN_TASKS_INLINE` (base/test True, prod False); `.env.example`.
- **R5** Signals (apps/pipeline/signals.py + AppsConfig.ready) — **contrato final (correção do review)**: (a) `CASE_STATUS_LLM_EXTRACTING` com `source == "ANONYMIZING"` → enqueue (entrada real; self-transitions não re-disparam); (b) `CASE_STATUS_LLM_SUMMARIZING` com `source == "LLM_EXTRACTING"` **E `actor_type == "user"`** → enqueue de retomada (bypass do NIR; o avanço natural do orquestrador tem ator `system` e NÃO re-enfileira — a task segue em execução própria). Ambos via `transaction.on_commit`; conflito de lock em async = no-op idempotente.
- **R6** Compose: serviço `worker-llm` (`Q_CLUSTER_NAME=llm`, imagem pronta — padrão dos workers existentes); `docker compose config` valida.
- **R7** `docs/adr/ADR-0008-llm-pipeline-per-type.md`: decisão (per-type + composição união; guardas; policy determinística consultiva prevalecendo; prior-case com fallback; fail-closed; modelos por env com benchmark operacional), alternativas (N chamadas por tipo; policy dentro do LLM; sem gate de divergência), consequências (custo 2 chamadas/caso; divergência depende de calibração; LLM2 refém da qualidade da LLM1).
- **R8** Testes: pipeline completo feliz (fakes LLM1/LLM2) → AWAITING_DOCTOR com artefatos (structured/policy/summary/suggested) e eventos das etapas na ordem; divergência → retém em LLM_EXTRACTING (não chama LLM2); bypass pelo intake → retomada roda SÓ policy/prior/LLM2 (LLM1 não re-executada — assert de chamadas nos fakes) → AWAITING_DOCTOR; policy recusa → sugestão recusada apesar de LLM sugerir aceitar; strictest no agregado; falha LLM2 → FAILED com motivo; reexecução em AWAITING_DOCTOR → no-op; signal: entrada (source ANONYMIZING) enfileira; avanço natural da task (actor system) NÃO enfileira duplicado; bypass (actor user) enfileira; lock respeitado; inline full-chain (create→…→AWAITING_DOCTOR) numa transação de teste.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1/R2 | `apps/pipeline/llm2_service.py`, `apps/cases/models.py` (+migration se pendente) | `test_llm2.py::test_summary_with_policy_precedence`, `::test_strictest_aggregate`, `::test_llm2_fail_closed`, `::test_payload_tokens_only` |
| R3 | `apps/pipeline/{orchestrator,tasks}.py` | `test_orchestrator.py::test_full_pipeline_to_awaiting_doctor`, `::test_divergence_retains_no_llm2`, `::test_resume_after_bypass_skips_llm1`, `::test_failure_fails_closed`, `::test_noop_when_awaiting` |
| R4 | `config/settings/{base,prod,test}.py`, `.env.example` | `rg -n "llm\|LLM_RUN_TASKS_INLINE" config/settings/base.py .env.example` |
| R5 | `apps/pipeline/{signals,apps}.py` | `test_signals.py::test_entry_enqueues`, `::test_natural_advance_system_actor_no_enqueue`, `::test_bypass_user_actor_enqueues` |
| R6 | `docker-compose.dev.yml` | `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet` |
| R7 | `docs/adr/ADR-0008*.md` | inspeção |
| R8 | `apps/pipeline/tests/{test_llm2,test_orchestrator,test_signals}.py` | `uv run pytest apps/pipeline/tests/` |

## RED

- Comando: `uv run pytest apps/pipeline/tests/test_orchestrator.py`
- Falha esperada: `ModuleNotFoundError: apps.pipeline.orchestrator`.

## GREEN / verificação local

- `uv run pytest apps/pipeline/tests/` — exit 0
- `uv run pytest apps/pipeline/tests/ apps/intake/tests/ apps/anonymization/tests/ apps/cases/tests/` — exit 0 (regressão cross-app)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`
- `docker compose -f docker-compose.yml -f docker-compose.dev.yml config --quiet`

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/pipeline/{llm2_service,orchestrator,tasks,signals,apps}.py
  - apps/pipeline/tests/{test_llm2,test_orchestrator,test_signals}.py
  - config/settings/{base,prod,test}.py
  - docker-compose.dev.yml
  - .env.example
  - docs/adr/ADR-0008-llm-pipeline-per-type.md
out_of_scope:
  - fila/presenter médico (07); agendamento (08); UI; benchmark no CI; novos estados; mudanças no anonymization
```

Escale ao parent se: a deduplicação de enqueue (R5) exigir mecanismo além de actor_type/estado (ex.: debounce); o full-chain inline revelar coordenação de locks não prevista.

## Critérios de aceitação

- [ ] R1–R8 comprovados; fail-a-fail: nenhum caminho leva texto real ou artefato inválido adiante
- [ ] Retomada pós-bypass não refaz LLM1 (assert de chamadas)
- [ ] Enqueue sem duplicação no avanço natural
- [ ] Gate parcial do slice verde
