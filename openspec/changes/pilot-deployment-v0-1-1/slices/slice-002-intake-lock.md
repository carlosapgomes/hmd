# Slice 002: INTAKE_ENABLED — bloqueio fail-closed de novos casos/uploads

## Objetivo

Setting env `INTAKE_ENABLED` (default true em base/dev/teste; **false em
prod**) que desliga, de forma fail-closed e sem efeito colateral, a criação
de novos casos com documentos/anexos e o reenvio/reprocessamento — no
boundary do SERVIÇO (qualquer caller) e com resposta amigável nas rotas de
POST.

## Contexto necessário

- Design D7 (`openspec/changes/pilot-deployment-v0-1-1/design.md`).
- `apps/intake/services.py`: `create_case_with_documents`,
  `create_corrected_resubmission`, `resubmit_case_documents` (ordem atual:
  validação → atomic → arquivos compensáveis → enqueue pós-commit; o guard
  entra no TOPO, antes de TUDO).
- `apps/intake/views.py`: POSTs de `intake:home`, `intake:case_resubmit`,
  `intake:gate_resubmit` (padrão de flash/redirect da app).
- Padrão de flag fail-closed em prod: `INTAKE_RUN_TASKS_INLINE` em
  `config/settings/{base,prod}.py` (default "false" em prod, "true" na
  base) — replicar exatamente para `INTAKE_ENABLED`.
- Padrão de teste de NÃO-enfileiramento: suítes do change 04
  (`INTAKE_RUN_TASKS_INLINE=False` + assert de enqueue/async_task).

## Requisitos verificáveis

- **R1** Setting: base `INTAKE_ENABLED = env bool default true`;
  `prod.py` default **false** (fail-closed como os flags irmãos);
  `.env.example` com comentário (fase 1 piloto = false; ativação futura
  junto a workers/egress).
- **R2** Guard de serviço `_assert_intake_enabled()` no TOPO das três
  funções de criação — `ValueError` nomeado ("envio de relatórios está
  desabilitado neste ambiente") ANTES de qualquer validação/escrita/arquivo;
  cobre qualquer caller (não só HTTP).
- **R3** Views: nos 3 POSTs, desligado → mensagem flash informativa +
  redirect de volta (sem traceback, sem 500); GETs seguem renderizando
  (nenhuma mudança de UI além da mensagem).
- **R4** Tests: POST de criação com `INTAKE_ENABLED=False` ⇒ **0** rows de
  Case/CaseDocument/CaseAttachment e **0** enfileiramentos (inline False +
  assert); idem reenvio corrigido e gate_resubmit; serviço direto levanta
  erro nomeado sem criar nada; com default true (dev/teste) tudo segue
  criando (regressões existentes intactas); prod default False verificado
  por import de settings com env dummy.

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) | Teste/check |
| --- | --- | --- |
| R1 | `config/settings/{base,prod}.py`, `.env.example` | assert em teste de settings |
| R2 | `apps/intake/services.py` | `test_create_disabled_raises_no_side_effect` ×3 |
| R3 | `apps/intake/views.py` | `test_home_post_disabled_*`, `test_resubmit_post_disabled_*`, `test_gate_resubmit_post_disabled_*` |
| R4 | `apps/intake/tests/test_intake_lock.py` | suíte do slice |

## Escopo e expected blast radius

```yaml
expected_files:
  - config/settings/base.py
  - config/settings/prod.py
  - apps/intake/services.py
  - apps/intake/views.py
  - apps/intake/tests/test_intake_lock.py
  - .env.example

out_of_scope:
  - liberação de casos retidos (release_retained_case não é upload); UI extra
  - settings de runtime/health (slice 001); compose (003); workflow (004)
```

## Plano de testes do slice

### RED

- `TEST_DB_PORT=55435 uv run pytest apps/intake/tests/test_intake_lock.py`
  → 404/coleção falha (setting/rota de bloqueio inexistentes).

### GREEN / verificação local

- `TEST_DB_PORT=55435 uv run pytest apps/intake/tests/ apps/accounts/tests/`
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .`

## Critérios de aceitação

- [ ] R1–R4; NENHUM caso/documento/anexo/tarefa criado com flag desligada
- [ ] Erro nomeado no serviço cobre callers não-HTTP
- [ ] Dev/teste (default true) sem regressão
- [ ] Gate parcial do slice verde
