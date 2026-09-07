# Slice 005: Revisão NIR do gate (liberar e reenviar documentos)

## Objetivo

Fechar o ciclo do gate: no detalhe de um caso retido, o NIR pode **liberar** (bypass — avança para `ANONYMIZING` com evento `CASE_GATE_BYPASSED`) ou **reenviar documentos** (substitui os PDFs, zera flag/texto/nº e reprocessa do início). Ambas disponíveis apenas enquanto o caso está retido.

## Contexto necessário (contexto zero)

- Slices 001–004 entregues: criação, task de processamento (retém em `PDF_EXTRACTING` com `manual_review_required`), meus casos/detalhe.
- Change 03: `complete_pdf_extraction` (`PDF_EXTRACTING → ANONYMIZING`, `*, user, role`), eventos canônicos em `apps/cases/events.py` (o slice 003 já adicionou `CASE_GATE_BYPASSED`; se necessário, garantir).
- Design: `../design.md` D4 (bypass usa transição existente — nenhum estado novo), D6 (`gate_release`/`gate_resubmit` POST, escopo por criador). Spec: Requirement "Revisão NIR do gate" (2 cenários).
- Referência de espírito: ats-web `scope_gate_bypass` (revisão manual nunca vai à fila sem NIR) — HMD adapta para retenção na extração.

## Requisitos

- **R1** `gate_release` (`POST /intake/cases/<uuid>/gate/release/`): serviço transacional com `select_for_update` no `Case` + re-check das pré-condições DENTRO da transação (caso do criador **e** `status=PDF_EXTRACTING` **e** `manual_review_required=True` — senão 404/400 sem efeito) + conflito se houver lock ativo não-expirado (`CaseLockConflictError`); efeito — `complete_pdf_extraction(*, user, role)` + evento `CASE_GATE_BYPASSED` (payload com o motivo original da retenção) na mesma transação; zera `manual_review_required`/`manual_review_reason`; redirect ao detalhe com mensagem; botão visível no detalhe apenas quando retido.
- **R2** `gate_resubmit` (`POST /intake/cases/<uuid>/gate/resubmit/`): mesmas pré-condições + concorrência de R1 (select_for_update, re-check, lock ativo conflita); recebe novos arquivos (mesma validação do slice 001: PDF-only, count/size); em exceção pós-gravação de arquivos físicos, limpeza compensatória best-effort (design D7); efeito transacional — remove documentos antigos, cria novos `CaseDocument` (positions novas), zera `extracted_text`/`manual_review_*`/`agency_record_number`/`agency_record_extracted_at`; após a transação reenfileira processamento (slice 003 — inline/async conforme settings); redirect ao detalhe.
- **R3** Nenhuma ação disponível para caso não-retido (status ≠ `PDF_EXTRACTING` ou sem flag): POST → 400/404 sem efeito colateral, coberto por teste.
- **R4** Testes: liberar retido → `ANONYMIZING` + flag zerada + evento `CASE_GATE_BYPASSED` com NIR como ator (user+role da sessão); liberar não-retido → sem efeito; reenviar com PDFs válidos do padrão → docs substituídos, flag/texto/nº zerados, caso reprocessa até `ANONYMIZING` **sem novo start** (PDF_EXTRACTING→…) com novos eventos; reenviar com arquivo inválido → nada muda + erro nomeia arquivo; caso alheio → 404 nas duas ações; **concorrência**: release e resubmit simultâneos sobre o mesmo caso → exatamente um vence, o outro vê conflito/400 sem efeito; **lock ativo de worker** → ação conflita (`CaseLockConflictError` tratada como 409/400).

## Matriz requisito → arquivo → teste/check

| Requisito | Arquivo(s) esperado(s) | Teste/check |
| --- | --- | --- |
| R1 | `apps/intake/views.py` (+service se fizer sentido), `templates/intake/case_detail.html` | `test_gate_actions.py::test_release_retained_advances`, `::test_release_not_retained_no_effect` |
| R2 | `apps/intake/{views,services}.py` | `::test_resubmit_replaces_and_reprocesses`, `::test_resubmit_invalid_file_no_effect` |
| R3 | `apps/intake/views.py` | `::test_release_not_retained_no_effect` (idem R1) + `::test_resubmit_not_retained_no_effect` |
| R4 | `apps/intake/tests/test_gate_actions.py` | `uv run pytest apps/intake/tests/test_gate_actions.py` |

## RED

- Comando: `uv run pytest apps/intake/tests/test_gate_actions.py`
- Falha esperada: 404 nas rotas de ação — revisão do gate não existe.

## GREEN / verificação local

- `uv run pytest apps/intake/tests/test_gate_actions.py` — exit 0
- `uv run pytest apps/intake/tests/` — exit 0 (regressão do app)
- `uv run pytest apps/cases/tests/` — exit 0 (regressão do núcleo)
- `uv run ruff check . && uv run ruff format --check . && uv run mypy .` — exit 0

## Escopo e expected blast radius

```yaml
expected_files:
  - apps/intake/{views,services,urls}.py
  - apps/intake/tests/test_gate_actions.py
  - templates/intake/case_detail.html   # botões de ação quando retido
allowed_incidental_files: []
out_of_scope:
  - reenvio corrigido como NOVO caso (corrects_case — change 09)
  - recuperação de caso FAILED (terminal; limitação registrada no design)
  - ações para outros papéis (doctor/scheduler chegam em 07/08)
  - novas transições FSM (usa complete_pdf_extraction existente)
```

Escale ao parent se: o resubmit precisar de nova transição FSM (não deve); validação de arquivo divergir da do slice 001 (reutilizar, não duplicar).

## Critérios de aceitação

- [ ] R1–R4 comprovados pelos comandos da matriz (2 cenários da spec cobertos)
- [ ] Ações restritas a caso retido do próprio criador (sem efeito colateral fora disso)
- [ ] Bypass grava evento com NIR como ator (user+papel ativo)
- [ ] Resubmit reprocessa do zero (texto/nº/flag zerados antes)
- [ ] Gate parcial do slice verde
